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
import osclib.conf

from osclib.cache import Cache
from osclib.conf import Config
from osclib.stagingapi import StagingAPI

from lxml import etree as ET
from influxdb import InfluxDBClient

osclib.conf.DEFAULT[r'openSUSE:(?P<project>[\d.]+)'] = osclib.conf.DEFAULT[r'openSUSE:(?P<project>Leap:[\d.]+)']

def get_request_list(*args, **kwargs):
    global _requests

    osc.core._search = osc.core.search
    osc.core.search = search

    osc.core._ET = osc.core.ET
    osc.core.ET = ET

    osc.core.get_request_list(*args, **kwargs)

    osc.core.search = osc.core._search

    osc.core.ET = osc.core._ET

    return _requests

def search(apiurl, queries=None, **kwargs):
    global _requests

    if "submit/target/@project='openSUSE:Factory'" in kwargs['request']:
        kwargs['request'] = xpath = osc.core.xpath_join(kwargs['request'], '@id>250000', op='and')
    #kwargs['request'] = xpath = osc.core.xpath_join(kwargs['request'], '@id>400000', op='and')
    #kwargs['request'] = xpath = osc.core.xpath_join(kwargs['request'], '@id>526000', op='and')

    requests = []
    queries['request']['limit'] = 1000
    queries['request']['offset'] = 0
    while True:
        collection = osc.core._search(apiurl, queries, **kwargs)['request']
        requests.extend(collection.findall('request'))

        if len(requests) == int(collection.get('matches')): # or len(requests) > 50000:
            break

        queries['request']['offset'] += queries['request']['limit']

    _requests = requests
    return {'request': ET.fromstring('<collection matches="0"></collection>')}

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
            if line.measurement == 'staging':
                # TODO lol ugly
                counters_tag = counters.setdefault(line.measurement + line.tags['id'], {})
            else:
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
        #print(line.timestamp, line.measurement, line.tags, line.fields)

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
    #Cache.PATTERNS['/search/request'] = Cache.TTL_LONG
    Cache.PATTERNS['/search/request'] = sys.maxint
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
    #requests = osc.core.get_request_list(apiurl, args.project,
    requests = get_request_list(apiurl, args.project,
                                         req_state=('accepted', 'revoked', 'superseded'),
                                         #req_type='submit', # TODO May make sense to query submit and delete seperately or alter the function to allow multiple to reduce massive result set
                                         exclude_target_projects=[args.project],
                                         withfullhistory=True) # withfullhistory requires ...osc
    #first = True
    print('processing {} requests'.format(len(requests)))
    for request in requests:
        request_id = int(request.get('id'))
        #print(request.find('state').get('name'))
        if request.find('state').get('name') != 'accepted':
            continue
        if request.find('action').get('type') != 'submit':
            continue # never staged by factory-staging

        #ET.dump(request.find('history'))
        created_at = date_parse(request.find('history').get('when'))
        final_at = date_parse(request.find('state').get('when'))
        final_at_history = date_parse(request.find('history[last()]').get('when'))
        if final_at_history > final_at:
            # Workaround for invalid dates: openSUSE/open-build-service#3858.
            final_at = final_at_history

        open_for = (final_at - created_at).total_seconds()
        #print(final_at - created_at)
        #print(open_for)
        ##delta = datetime.utcnow() - created
        ##request.set('aged', str(delta.total_seconds() >= self.request_age_threshold))
        ##break
        ##print(request.reqid)
        #print(request.get('id'))

        #print(timestamp(final_at))
        
        if len(request.xpath('review[@by_group="factory-staging"]/history/@when')) == 0:
            print('skippy mcskipp: {}'.format(request_id))
            continue
        
        first_staged = date_parse(request.xpath('review[@by_group="factory-staging"]/history/@when')[0])
        
        staged_count = len(request.findall('review[@by_group="factory-staging"]/history'))
        line('request', {'id': request_id}, {'total': open_for,
                                             'staged_count': staged_count,
                                             'staged_first': (first_staged - created_at).total_seconds(),
                                             }, False, timestamp(final_at))
        line('request_staged_first', {'id': request_id}, {'value': (first_staged - created_at).total_seconds()}, False, timestamp(first_staged))
        # TODO likely want to break these stats out into different measurements
        # so that the timestamp can be set for the particular stat
        # for example staged_first as first_staged timestamp instead of final_at
        
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
        #line('total', {'request': request_id, 'event': 'select'}, {'backlog': -1}, True, timestamp(first_staged))
        #line('total', {}, {'backlog': -1, 'staged': 1}, True, timestamp(first_staged))
        
        line('total', {'request': request_id, 'event': 'close'}, {'backlog': -1, 'open': -1}, True, timestamp(final_at))
        
        # TODO review totals
        #for s in request.xpath('review/history/@when')
        
        # assume declined/revoked (if no further staging actions) is when unstaged...with
        # note about correcting using review history later
        # do the review times get broken out separately or what
        #break
        
        #i += 1
        #if i == 400:
            #break
        
        #continue
        
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
        #for review in root.xpath('review[@by_group="factory-staging" and @state="accepted"]'):
        number = 1
        for review in root.xpath('review[contains(@by_project, "{}:Staging:")]'.format(args.project)):
        #for review in root.xpath('review/*[history]'):
            history = review.find('history') # removed when parsed by request
            #print(review.get('when'))
            #print(review.get('by_project'))

            if not review.get('who'):
                print(request_id)
                # TODO apparently a review can be in state="obsoleted" at which point
                # can only tell who staged by looking at previous accepted factory-staging
                # only 7 in all of Leap:42.3, but rather dumb
                # TODO also want who unstaged? to show who removed
            staged_at = date_parse(review.get('when'))

            #by_project = review.get('by_project')
            #history_elements = root.xpath('history[comment[text()="Picked {}"]]'.format(by_project))
            #if len(history_elements) > 1:
                ##print('confused', request_id, by_project)
                #pass
            #elif len(history_elements):
                #staged_at_history = date_parse(history_elements[0].get('when'))
                #if staged_at_history > staged_at:
                    #staged_at = staged_at_history
                    #print('swapped')

            project_type = 'adi' if api.is_adi_project(review.get('by_project')) else 'letter'
            short = api.extract_staging_short(review.get('by_project'))
            line('staging', {'id': short, 'type': project_type, 'request': request_id, 'event': 'select'}, {'count': 1}, True,
                 timestamp(staged_at))
            line('user', {'request': request_id, 'event': 'select', 'user': review.get('who'), 'number': number}, {'count': 1}, False,
                 timestamp(staged_at))

            line('total', {'request': request_id, 'event': 'select'}, {'backlog': -1, 'staged': 1}, True, timestamp(staged_at))

            if history is not None:
                #print(':{}'.format(history.get('when')))
                unselected_at = date_parse(history.get('when'))
            else:
                unselected_at = final_at
            # assumption is that if declined and re-opened request would have been
            # repaired (thus review closed, so only the last one could be in this
            # un-repaired state.
            line('staging', {'id': short, 'type': project_type, 'request': request_id, 'event': 'unselect'}, {'count': -1}, True, timestamp(unselected_at))
            number += 1
            
            line('total', {'request': request_id, 'event': 'unselect'}, {'backlog': 1, 'staged': -1}, True, timestamp(unselected_at))
        #ET.dump(request.to_xml())
        #for review in request.reviews:
            #print(review.to_str())
        #break
        #sys.exit()
        continue
        
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

    db = args.project
    client = InfluxDBClient('localhost', 8086, 'root', 'root', db)
    client.drop_database(db)
    client.create_database(db)
    client.write_points(points, 's')
    result = client.query('select count(backlog) from total;')
    print("Result: {0}".format(result))
    
    leap_423_schedule = {
        '2017-04-01': 'integration of SLE sources',
        '2017-05-21': 'major version update freeze',
        '2017-06-06': 'SLE RC: base system freeze',
        '2017-06-25': 'package freeze',
        '2017-07-26': 'final release',
    }
    leap_422_schedule = {
        '2016-05-24': 'Alpha 1',
        '2016-06-21': 'Alpha 2',
        '2016-07-20': 'Alpha 3 - base system freeze',
        '2016-08-31': 'Beta 1',
        '2016-09-22': 'Beta 2 (delayed one day)',
        '2016-10-05': 'Beta 3 - package freeze',
        '2016-10-18': 'RC1',
        '2016-11-02': 'RC2',
        '2016-11-16': 'Release',
    }
    
    #rsync rsync.opensuse.org::opensuse-full-with-factory/opensuse/tumbleweed/iso/Changes.2017* | grep -oP "Changes\.\K\d+"
    tumbleweed_schedule = [
        20170104,
        20170109,
        20170110,
        20170112,
        20170117,
        20170118,
        20170120,
        20170121,
        20170123,
        20170124,
        20170125,
        20170127,
        20170128,
        20170129,
        20170130,
        20170131,
        20170201,
        20170203,
        20170204,
        20170205,
        20170206,
        20170207,
        20170208,
        20170209,
        20170211,
        20170212,
        20170213,
        20170214,
        20170215,
        20170216,
        20170218,
        20170219,
        20170224,
        20170225,
        20170226,
        20170227,
        20170228,
        20170302,
        20170303,
        20170304,
        20170305,
        20170308,
        20170309,
        20170310,
        20170311,
        20170314,
        20170315,
        20170316,
        20170317,
        20170318,
        20170320,
        20170322,
        20170324,
        20170328,
        20170329,
        20170331,
        20170403,
        20170406,
        20170407,
        20170413,
        20170414,
        20170417,
        20170418,
        20170419,
        20170420,
        20170424,
        20170425,
        20170426,
        20170503,
        20170505,
        20170510,
        20170516,
        20170521,
        20170522,
        20170524,
        20170529,
        20170601,
        20170602,
        20170604,
        20170605,
        20170607,
        20170608,
        20170609,
        20170610,
        20170612,
        20170613,
        20170615,
        20170616,
        20170617,
        20170618,
        20170619,
        20170620,
        20170622,
        20170625,
        20170626,
        20170628,
        20170629,
        20170630,
        20170701,
        20170702,
        20170703,
        20170704,
        20170706,
        20170707,
        20170708,
        20170709,
        20170710,
        20170712,
        20170722,
        20170723,
        20170724,
        20170725,
        20170726,
        20170728,
        20170729,
        20170730,
        20170801,
        20170802,
        20170804,
        20170806,
        20170808,
        20170810,
        20170815,
        20170816,
        20170817,
        20170819,
        20170821,
        20170822,
        20170823,
        20170825,
        20170830,
        20170831,
        20170904,
        20170905,
        20170907,
        20170908,
        20170909,
        20170911,
        20170913,
    ]
    
    if db.endswith('42.3'):
        release_schedule = leap_423_schedule
    elif db.endswith('42.2'):
        release_schedule = leap_422_schedule
    elif db.endswith('Factory'):
        release_schedule = {}
        for date in tumbleweed_schedule:
            date = str(date)
            release_schedule['{}-{}-{}'.format(date[0:4], date[4:6], date[6:8])] = 'Snapshot: {}'.format(date)
    else:
        return
    
    points = []
    for date, description in release_schedule.items():
        points.append({
            'measurement': 'release_schedule',
            'fields': {'description': description},
            'time': timestamp(datetime.strptime(date, '%Y-%m-%d')),
            })
    client.write_points(points, 's')


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
