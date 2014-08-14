#!/usr/bin/python
# coding: utf-8

import time

from puliclient import Task, Graph
from optparse import OptionParser

def process_args():
    usage = "Graph submission example"
    desc=""" """
    parser = OptionParser(usage=usage, description=desc, version="%prog 0.1" )
    parser.add_option("-n", "--name",       action="store", dest="jobname",     type=str,   default="Example job",         help="")
    parser.add_option("-s", "--server",     action="store", dest="hostname",    type=str,   default="puliserver",   help="Specified a target host to send the request")
    parser.add_option("-p", "--port",       action="store", dest="port",        type=int,   default=8004,           help="Specified a target port")
    parser.add_option("-x", "--execute",    action="store_true", dest="execute",                                    help="Override submit param and executes job locally")
    parser.add_option("-d", "--display",    action="store_true", dest="dump",                                       help="Print graph json representation before process")

    parser.add_option("--min",              action="store", dest="min",    type=int,   default=20,             help="")
    parser.add_option("--max",              action="store", dest="max",    type=int,   default=50,             help="")
    parser.add_option("--num",              action="store", dest="num",    type=int,   default=10,             help="")

    options, args = parser.parse_args()
    return options, args



if __name__ == '__main__':
    (options, args) = process_args()

    command = "sleep `shuf -i %d-%d -n 1`" % (options.min, options.max )
    args =  { "args":command, "delay": options.min, "start":1, "end":options.num, "packetSize":1 }
    tags =  { "prod":"test", "shot":"test", "nbFrames":options.num }

    #
    # Create custom graph
    #
    timer = time.time() + 600
    simpleTask = Task( name=options.jobname, arguments=args, tags=tags, runner="puliclient.contrib.commandlinerunner.CommandLineRunner", timer=timer )
    # simpleTask = Task( name="T-Generic", arguments=args, tags=tags, runner="puliclient.contrib.debug.WaitRunner" )
    graph = Graph( options.jobname, simpleTask, tags=tags, poolName='default' )

#    graph.addNewTask( name="T1", arguments={ "args": command, "start":1, "end":5, "packetSize":1 }, tags={ "prod":"test", "shot":"test", "nbFrames":5}, runner=runner )
#
#
#    g1 = graph.addNewTaskGroup( name="group1", tags=tags )
#    g1.addTask( simpleTask )
#    g1.addNewTask( name="T2", arguments={ "args": command, "start":1, "end":1, "packetSize":1 }, tags={ "prod":"test", "shot":"test", "nbFrames":1}, runner=runner )
#    g1.addNewTask( name="T3", arguments={ "args": command, "start":1, "end":1, "packetSize":1 }, tags={ "prod":"test", "shot":"test", "nbFrames":1}, runner=runner )
#
#    graph.addNewTask( name="T4", arguments={ "args": command, "start":1, "end":10, "packetSize":1 }, tags={ "prod":"test", "shot":"test", "nbFrames":10}, runner=runner )

    if options.dump:
        print graph

    #
    # Execute
    #
    if options.execute:
        graph.execute()
    else:
        graph.submit(options.hostname, options.port)

