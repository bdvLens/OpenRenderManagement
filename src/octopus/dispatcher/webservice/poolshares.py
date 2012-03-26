'''
Created on Dec 16, 2009

@author: acs
'''
from octopus.core.communication.http import Http404, Http400, HttpConflict
from octopus.dispatcher.model.pool import PoolShare, PoolShareCreationException
from octopus.core.tools import json

from octopus.core.framework import BaseResource, queue

__all__ = []

class PoolSharesResource(BaseResource):
    @queue
    def get(self):
        poolShares = self.getDispatchTree().poolShares.values()
        self.writeCallback({
            'poolshares': dict(((poolShare.id, poolShare.to_json()) for poolShare in poolShares))
            })
    
    @queue
    def post(self):
        dct = self.getBodyAsJSON()
        for key in ('poolName', 'nodeId', 'maxRN'):
            if not key in dct:
                return Http400("Missing key %r" % key)
        poolName = str(dct['poolName'])
        nodeId = int(dct['nodeId'])
        maxRN = int(dct['maxRN'])
        # get the pool object
        if not poolName in self.getDispatchTree().pools:
            return HttpConflict("Pool %s is not registered" % poolName)
        pool = self.getDispatchTree().pools[poolName]
        # get the node object
        if not nodeId in self.getDispatchTree().nodes:
            return HttpConflict("No such node %r" % nodeId)
        node = self.getDispatchTree().nodes[nodeId]
        # create the poolShare
        try:
            poolShare = PoolShare(None, pool, node, maxRN)
            self.getDispatchTree().poolShares[poolShare.id] = poolShare
            # return the response
#            response = HttpResponse(201)
#            response['Location'] = '/poolshares/%r/' % poolShare.id
#            response.writeCallback(json.dumps(poolShare.to_json()))
            self.set_header('Location', '/poolshares/%r/' % poolShare.id)
            self.writeCallback(json.dumps(poolShare.to_json()))
        except PoolShareCreationException:
            return HttpConflict("PoolShare of pool for this node already exists")

class PoolShareResource(BaseResource):
    @queue
    def get(self, id):
        try:
            poolShare = self.getDispatchTree().poolShares[int(id)]
        except KeyError:
            return Http404("No such poolshare")
        self.writeCallback({
            'poolshare': poolShare.to_json()
        })

class PoolShareMaxrnResource(BaseResource):
    @queue
    def post(self, id):
        try:
            poolShare = self.getDispatchTree().poolShares[int(id)]
        except KeyError:
            return Http404("No such poolshare")
        
        
