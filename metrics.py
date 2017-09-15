#!/usr/bin/python

import argparse
from collections import namedtuple
import os
import sys

import osc.conf
import osc.core

from osclib.cache import Cache
from osclib.conf import Config
from osclib.stagingapi import StagingAPI

try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

#CountChange = namedtuple('CountChange', ('timestamp', 'increase'))
InfluxLine = namedtuple('InfluxLine', ('tags', 'fields', 'delta', 'timestamp'))


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
    bucket_lines = []
    requests = osc.core.get_request_list(apiurl, args.project,
                                         req_state=('accepted', 'revoked', 'superseded'),
                                         #req_type='submit', # TODO May make sense to query submit and delete seperately or alter the function to allow multiple to reduce massive result set
                                         withfullhistory=True) # withfullhistory requires ...osc
    for request in requests:
        print(request.find('state').get('name'))
        if request.find('state').get('name') != 'accepted':
            continue
        #break
        #print(request.reqid)
        print(request.get('id'))
        #root = request.to_xml()
        #ET.dump(root)
        root = request
        for review in root.findall('review'):
            history = review.find('history') # removed when parsed by request
            print(review.get('when'))
            print(review.get('by_project'))
            if history:
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
