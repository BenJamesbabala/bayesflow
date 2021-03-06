import numpy as np
import tensorflow as tf

import uuid

import bayesflow as bf
import bayesflow.util as util
from bayesflow.models.q_distributions import ObservedQDistribution, GaussianQDistribution

class ConditionalDistribution(object):
    """
    
    Generic object representing a conditional distribution: a single output conditioned on some set of inputs. 
    Unconditional distributions are the special case where the inputs are the empty set. 

    The inputs are assumed to be random variables described by their own respective distributions. 
    Thus the object graph implicitly represents a directed graphical model (Bayesian network).

    """

    
    def __init__(self, output_shape=None, dtype=None, minibatch_scale_factor = None, name=None, **kwargs):

        if name is None:
            name = str(self.__class__.__name__) + "_" + str(uuid.uuid4().hex)[:6]
            print "constructed name", name
        self.name = name

        self.minibatch_scale_factor = minibatch_scale_factor

        # store map of input param names to the nodes modeling those params
        self.input_nodes = {}
        for input_name in self.inputs():
            # inputs can be provided as constants, or nodes modeled by bayesflow distributions.
            # if they are constants, we create a node to represent that. 
            if isinstance(kwargs[input_name], ConditionalDistribution):
                self.input_nodes[input_name] = kwargs[input_name]
            elif kwargs[input_name] is None:
                # allow optional args to be passed as None
                pass
            else:
                constant_node = FlatDistribution(kwargs[input_name], fixed=True, name=self.name+"_"+input_name+"_fixed")
                self.input_nodes[input_name] = constant_node

                
        # compute the shape of the output at this node
        if output_shape is not None:
            self.output_shape = output_shape
        else:
            input_shapes = {name + "_shape": node.output_shape for (name,node) in self.input_nodes.items()}
            self.output_shape = self._compute_shape(**input_shapes)

        if dtype is not None:
            self.dtype = dtype
        else:
            input_dtypes = {name + "_dtype": node.dtype for (name,node) in self.input_nodes.items()}
            self.dtype = self._compute_dtype(**input_dtypes)
        
        # compute the list of all ancestor nodes in the graph, by
        # merging the ancestor lists of the parent nodes.  Storing
        # this for every node is slightly inefficient, but makes for
        # simpler code by avoiding the need for a graph traversal when
        # constructing joint quantities like the ELBO. This could be
        # optimized if the ancestor lists ever become a performance
        # bottleneck.
        self.ancestors = set( [self,] + [ancestor for node in self.input_nodes.values() for ancestor in node.ancestors ] )
    
        self._sampled_value = None
        self._sampled_value_seed = None

        self._q_distribution = None
        
    def sample(self, seed=0):
        
        if seed != self._sampled_value_seed:
            input_samples = {name: node.sample(seed=seed) for (name,node) in self.input_nodes.items()}        

            # add a salt to ensure we get independent samples at each node
            salt = self.name.__hash__()
            local_seed = (seed + salt) % (2**32)
            np.random.seed(local_seed)
            
            self._sampled_value_seed = seed
            self._sampled_value = self._sample(**input_samples)
        
        return self._sampled_value
    
    def elbo_term(self):
        input_qs = {"q_"+name: node.q_distribution() for (name,node) in self.input_nodes.items()}

        q = self.q_distribution()
        with tf.name_scope(self.name + "_Elogp") as scope:
            expected_lp = self._expected_logp(q_result = q, **input_qs)
        entropy = q.entropy()
        
        if self.minibatch_scale_factor is not None:
            expected_lp *= self.minibatch_scale_factor
            entropy *= self.minibatch_scale_factor

        return expected_lp, entropy

    def _expected_logp(self, **kwargs):
        # default implementation: compute E_q[ log p(x) ] as a Monte Carlo sample.
        samples = {}
        for (q_key, qdist) in kwargs.items():
            assert(q_key.startswith("q_"))
            key = q_key[2:]
            samples[key] = qdist.sample
        return self._logp(**samples)

    def __str__(self):
        return self.name + "_" + str(type(self))

    def q_distribution(self):
        
        if self._q_distribution is None:
            default_q = self.default_q()

            # explicitly use the superclass method since some subclasses
            # may redefine attach_q to prevent user-attached q's
            ConditionalDistribution.attach_q(self, default_q)
            
        return self._q_distribution
    
    def attach_q(self, q_distribution):
        # TODO check that the types and shape of the Q distribution match

        if self._q_distribution is not None:
            raise Exception("trying to attach Q distribution %s at %s, but another distribution %s is already attached!" % (self._q_distribution, self, self._q_distribution))

        assert(self.output_shape == q_distribution.output_shape)
        
        self._q_distribution = q_distribution
    
    def observe(self, observed_val):
        qdist = ObservedQDistribution(observed_val)
        self.attach_q(qdist)
        return qdist

    def default_q(self):
        raise Exception("default Q distribution not implemented!")

    def init_q_true(self):
        for name, node in self.input_nodes.items():
            node.init_q_true()
        
        qdist = self.q_distribution()
        if not isinstance(qdist, ObservedQDistribution):
            try:
                qdist.initialize_to_value(self._sampled_value)
                print "initialized", self.name, qdist
            except Exception as e:
                print "cannot initialize node", self.name, "qdist", qdist, e
        
class FlatDistribution(ConditionalDistribution):

    """
    A "dummy" distribution object representing a flat prior. It requires a "default" value that will
    be returned when sampling from this distribution. 
    
    Parameters with known or fixed values can be represented using a flat prior and an ObservedQDistribution 
    to fix them to the given value. 
    """
    
    def __init__(self, value, fixed=True, **kwargs):
        try:
            self.dtype = value.dtype
        except:
            self.dtype = np.float32
        
        self.value = np.asarray(value, dtype=self.dtype) 
        output_shape = self.value.shape        
        super(FlatDistribution, self).__init__(output_shape = output_shape, **kwargs)

        if fixed:
            qdist = ObservedQDistribution(self.value)
            self.attach_q(qdist)
        
    def inputs(self):
        return ()
        
    def _sample(self, seed=0):
        return self.value
    
    def _compute_dtype(self):
        return self.dtype

    def _logp(self, result):
        return tf.constant(0.0, dtype=tf.float32)


                                    
