#!/usr/bin/python
# -*- coding: utf8 -*-
from __future__ import absolute_import

"""
"""
__author__ = "Jerome Samson"
__copyright__ = "Copyright 2014, Mikros Image"

try:
    import simplejson as json
except Exception:
    import json

class JsonModel():
    """
    Add serialization capability to any object
    """
    def toJson(self, indent=0):
        return json.dumps(self, default=lambda o: o.__dict__, sort_keys=True, indent=indent)
