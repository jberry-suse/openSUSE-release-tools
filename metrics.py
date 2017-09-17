#!/usr/bin/python

import argparse
from collections import namedtuple
from datetime import datetime
from dateutil.parser import parse as date_parse
#dateutil.parser.parse
import os
import sys

import osc.conf
import osc.core

from osclib.cache import Cache
from osclib.conf import Config
from osclib.stagingapi import StagingAPI

from lxml import etree as ET
from influxdb import InfluxDBClient

#CountChange = namedtuple('CountChange', ('timestamp', 'increase'))
InfluxLine = namedtuple('InfluxLine', ('measurement', 'tags', 'fields', 'delta', 'timestamp'))
#InfluxLine = namedtuple('InfluxLine', ('measurement', 'tags', 'fields', 'delta', 'time'))
#InfluxLine.get = InfluxLine.getattr(InfluxLine, 'getattr')
InfluxLine.get = lambda self, key, default=None: getattr(self, key)


class Staging(object):
    def __init__(self, letter):
        self.letter = letter
        self.start = None
        self.requests = []

    def add(self, request):
        if len(self.requests) == 0:
            self.start = request.statehistory[0].when
        self.requests.append(int(request.reqid))

    def remove(self, request):
        self.requests.remove(int(request.reqid))

lines = []
timestamp_earliest = sys.maxint

def line(*args):
    global lines, timestamp_earliest

    line = InfluxLine(*args)
    lines.append(line)

    timestamp_earliest = min(timestamp_earliest, line.timestamp)

def timestamp(datetime):
    return int(datetime.strftime('%s'))

def walk_lines(lines, target):
    counters = {}
    #lines = sorted(lines, key=lambda l: l.timestamp)
    for line in sorted(lines, key=lambda l: l.timestamp):
        #print(line.timestamp)
        if line.delta:
            #counters_tag = counters.setdefault(line.tags, {})
            #key = '{}::{}'.format(line.measurement, line.tags['target'])
            #counters_tag = counters.setdefault(key, {})
            counters_tag = counters.setdefault(line.measurement, {})
            for key, value in line.fields.items():
                #counter = counters_tag.setdefault(key, 0)
                #print(key, counter, value)
                #counter += value
                #counters_tag[key] = counter
                #line.fields[key] = counter
                #line.fields[key] = counters_tag[key] = counters_tag.setdefault(key, 0) + value
                counters_tag[key] = counters_tag.setdefault(key, 0) + value
                #line.fields[key] = counters_tag[key]
            line.fields.update(counters_tag)

            #print(counters)

        #line.tags['target'] = target
        print(line.timestamp, line.measurement, line.tags, line.fields)

#def start_entries(first_entry):
    #line('total', {}, {'backlog': 0, 'ignore': 0, 'open': 0, 'staged': 0},
                 ##True, timestamp(created_at) - 1)

def main(args):
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug
    apiurl = osc.conf.config['apiurl']

    Cache.CACHE_DIR = os.path.expanduser('~/.cache/osc-plugin-factory-metrics')
    Cache.PATTERNS['/request/\d+\?withfullhistory=1'] = sys.maxint #TODO only if final state
    #Cache.PATTERNS["/search/request.*target/@project='([^']+)'"] = Cache.TTL_LONG # TODO Urlencoded so no match
    Cache.PATTERNS['/search/request'] = Cache.TTL_LONG
    Cache.init()
    #print(Cache.PATTERNS)

    # TODO This type of logic is also used in ReviewBot now
    Config(args.project)
    api = StagingAPI(apiurl, args.project)
    stagings = {}
    for letter in api.get_staging_projects_short():
        stagings[letter] = Staging(letter)
    #print(stagings)

    i = 0
    #bucket_lines = []
    requests = osc.core.get_request_list(apiurl, args.project,
                                         req_state=('accepted', 'revoked', 'superseded'),
                                         #req_type='submit', # TODO May make sense to query submit and delete seperately or alter the function to allow multiple to reduce massive result set
                                         exclude_target_projects=[args.project],
                                         withfullhistory=True) # withfullhistory requires ...osc
    #first = True
    for request in requests:
        request_id = int(request.get('id'))
        print(request.find('state').get('name'))
        if request.find('state').get('name') != 'accepted':
            continue
        if request.find('action').get('type') != 'submit':
            continue # never staged by factory-staging
        
        #ET.dump(request.find('history'))
        created_at = date_parse(request.find('history').get('when'))
        final_at = date_parse(request.find('state').get('when'))
        
        open_for = (final_at - created_at).total_seconds()
        print(final_at - created_at)
        print(open_for)
        #delta = datetime.utcnow() - created
        #request.set('aged', str(delta.total_seconds() >= self.request_age_threshold))
        #break
        #print(request.reqid)
        print(request.get('id'))

        print(timestamp(final_at))
        
        
        first_staged = date_parse(request.xpath('review[@by_group="factory-staging"]/history/@when')[0])
        
        # TODO If first entry might as well add a 0 entry
        
        #total,target=openSUSE:Factory backlog=10,ignore=7,open=1337
#staging,target=openSUSE:Factory,id=A state=building,count=7
#request,target=openSUSE:Factory,source=server:php:applications,id=1234,state=accepted backlog=1234,time_to_first=2334,moved=1234 1239019535 (of accept)
        #if first:
            #print('FIRST!!!!!!')
            #line('total', {}, {'backlog': 0, 'ignore': 0, 'open': 0, 'staged': 0},
                 #True, timestamp(created_at) - 1)
            #first = False
        
        #line('total', {'request': request_id, 'event': 'create'}, {'backlog': 1}, True, timestamp(created_at))
        #line('total', {'request': request_id, 'event': 'select'}, {'backlog': -1}, True, timestamp(first_staged))
        
        line('total', {'request': request_id, 'event': 'create'}, {'backlog': 1, 'open': 1}, True, timestamp(created_at))
        line('total', {'request': request_id, 'event': 'select'}, {'backlog': -1}, True, timestamp(first_staged))
        #line('total', {}, {'backlog': -1, 'staged': 1}, True, timestamp(first_staged))
        
        line('total', {'request': request_id, 'event': 'create'}, {'open': -1}, True, timestamp(final_at))
        
        # TODO review totals
        #for s in request.xpath('review/history/@when')
        
        # assume declined/revoked (if no further staging actions) is when unstaged...with
        # note about correcting using review history later
        # do the review times get broken out separately or what
        #break
        
        #i += 1
        #if i == 400:
            #break
        
        continue
        
        #bucket_lines.append(InfluxLine('total', {}, {'backlog': 1}, True, timestamp(created_at)))
        #bucket_lines.append(InfluxLine('total', {}, {'backlog': -1}, True, timestamp(first_staged)))
        
        #bucket_lines.append(InfluxLine('bucket',
                                       #{'target': args.project, 'id': 'backlog'},
                                       #{'count': 1}, True, timestamp(created_at)))
        #bucket_lines.append(InfluxLine('bucket',
                                       #{'target': args.project, 'id': 'backlog'},
                                       #{'count': -1}, True, timestamp(first_staged)))

        #print(bucket_lines)

        #root = request.to_xml()
        #ET.dump(root)
        root = request
        for review in root.findall('review'):
            history = review.find('history') # removed when parsed by request
            print(review.get('when'))
            print(review.get('by_project'))
            if history is not None:
                print(':{}'.format(history.get('when')))
        #ET.dump(request.to_xml())
        #for review in request.reviews:
            #print(review.to_str())
        break
        
        # TODO request type not delete or submit
        print(request.state.to_str())
        print(request.state.name)
        if request.state.name != 'accepted':
            continue
        #break
        #print(request)
        #print(dir(request))
        #print(ET.dump(request.to_xml()))

        #request = osc.core.get_request(apiurl, request.reqid)
        print(request)
        
        for review in request.reviews:
            print(review.to_str())
        #print(request.statehistory[0].when)
        for statehistory in request.statehistory:
            print(statehistory.name)
            print(statehistory.who)
            print(statehistory.when)
            print(statehistory.description)
            print(statehistory.comment)
            print(statehistory.to_str())
            #print(statehistory.)
            print('=====')
        break
        i += 1
        if i == 4:
            break

    #request = osc.core.get_request(apiurl, str(461992))
    #print(request)
    
    
    # Create starter line so all values are inherited.
    line('total', {}, {'backlog': 0, 'ignore': 0, 'open': 0, 'staged': 0},
                True, timestamp_earliest - 1)

    #walk_lines(bucket_lines, args.project)
    walk_lines(lines, args.project)
    
    points = []
    for line2 in lines:
        points.append({
            'measurement': line2.measurement,
            'tags': line2.tags,
            'fields': line2.fields,
            'time': line2.timestamp,
            })

    client = InfluxDBClient('localhost', 8086, 'root', 'root', 'obs')
    client.drop_database('obs')
    client.create_database('obs')
    client.write_points(points, 's')
    result = client.query('select backlog from total;')
    print("Result: {0}".format(result))


if __name__ == '__main__':
    description = '...'
    parser = argparse.ArgumentParser(description=description)
    # TODO influxdb line protocol output directory
    parser.add_argument('-A', '--apiurl', metavar='URL', help='OBS instance API URL')
    parser.add_argument('-d', '--debug', action='store_true', help='print info useful for debugging')
    #parser.add_argument('-p', '--project', default='openSUSE:Factory', metavar='PROJECT', help='OBS project')
    parser.add_argument('-p', '--project', default='openSUSE:Leap:42.3', metavar='PROJECT', help='OBS project')
    parser.add_argument('--limit', type=int, default='0', help='limit number') # TODO
    args = parser.parse_args()

    sys.exit(main(args))
