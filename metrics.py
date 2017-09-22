#!/usr/bin/python

import argparse
from collections import namedtuple
from datetime import datetime
from dateutil.parser import parse as date_parse
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

InfluxLine = namedtuple('InfluxLine', ('measurement', 'tags', 'fields', 'delta', 'timestamp'))


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
    for line in sorted(lines, key=lambda l: l.timestamp):
        if line.delta:
            if line.measurement == 'staging':
                # TODO lol ugly
                counters_tag = counters.setdefault(line.measurement + line.tags['id'], {})
            elif line.measurement == 'review_count':
                counters_tag = counters.setdefault(line.measurement + '_'.join(line.tags['key']), {})
                #del line.tags['key']
            elif line.measurement == 'priority':
                counters_tag = counters.setdefault(line.measurement + line.tags['level'], {})
            else:
                counters_tag = counters.setdefault(line.measurement, {})
            for key, value in line.fields.items():
                counters_tag[key] = counters_tag.setdefault(key, 0) + value
            line.fields.update(counters_tag)

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

    # TODO This type of logic is also used in ReviewBot now
    Config(args.project)
    api = StagingAPI(apiurl, args.project)

    i = 0
    requests = get_request_list(apiurl, args.project,
                                         req_state=('accepted', 'revoked', 'superseded'),
                                         exclude_target_projects=[args.project],
                                         withfullhistory=True)
    print('processing {} requests'.format(len(requests)))
    swap_staged_user = 0
    swap_staged_user_miss = 0
    for request in requests:
        request_id = int(request.get('id'))
        if request.find('state').get('name') != 'accepted':
            continue
        if request.find('action').get('type') != 'submit':
            continue # never staged by factory-staging

        created_at = date_parse(request.find('history').get('when'))
        final_at = date_parse(request.find('state').get('when'))
        final_at_history = date_parse(request.find('history[last()]').get('when'))
        if final_at_history > final_at:
            # Workaround for invalid dates: openSUSE/open-build-service#3858.
            final_at = final_at_history

        open_for = (final_at - created_at).total_seconds()

        if len(request.xpath('review[@by_group="factory-staging"]/history/@when')) == 0:
            print('skippy mcskipp: {}'.format(request_id))
            continue

        first_staged = date_parse(request.xpath('review[@by_group="factory-staging"]/history/@when')[0])
        ready_to_accept = request.xpath('review[contains(@by_project, "{}:Staging:adi:") and @state="accepted"]/history[comment[text() = "ready to accept"]]/@when'.format(args.project))
        if len(ready_to_accept):
            ready_to_accept = date_parse(ready_to_accept[0])
            ready = (final_at - ready_to_accept).total_seconds()

            # TODO Either need to merge indentical timestamp lines or use separate measurements
            #line('total', {'event': 'ready'}, {'ready': 1}, True, timestamp(ready_to_accept))
            #line('total', {'event': 'ready'}, {'ready': -1}, True, timestamp(final_at))
            line('ready', {}, {'count': 1}, True, timestamp(ready_to_accept))
            line('ready', {}, {'count': -1}, True, timestamp(final_at))
        else:
            ready = None

        staged_count = len(request.findall('review[@by_group="factory-staging"]/history'))
        request_fields = {'total': open_for,
                          'staged_count': staged_count,
                          'staged_first': (first_staged - created_at).total_seconds(),
                          }
        if ready:
            request_fields['ready'] = ready

        request_tags = {}

        first_staged_review = request.xpath('review[contains(@by_project, "{}:Staging:")]'.format(args.project))
        if len(first_staged_review):
            by_project = first_staged_review[0].get('by_project')
            request_tags['type'] = 'adi' if api.is_adi_project(by_project) else 'letter'

            # TODO track through revisions
            #splitter_whitelist = osc.conf.config.get('splitter-whitelist')
            if args.project.startswith('openSUSE:Factory'):
                splitter_whitelist = 'B C D E F G H I J'.split()
                if splitter_whitelist:
                    short = api.extract_staging_short(by_project)
                    request_tags['whitelisted'] = short in splitter_whitelist
                    #print(by_project, short, request_tags['whitelisted'])
            else:
                # All letter where whitelisted since no restriction.
                request_tags['whitelisted'] = request_tags['type'] == 'letter'
            #print(request_tags)

        line('request', request_tags, request_fields, False, timestamp(final_at))
        line('request_staged_first', request_tags, {'value': (first_staged - created_at).total_seconds()}, False, timestamp(first_staged))
        # TODO likely want to break these stats out into different measurements
        # so that the timestamp can be set for the particular stat
        # for example staged_first as first_staged timestamp instead of final_at
        line('total', {'event': 'create'}, {'backlog': 1, 'open': 1}, True, timestamp(created_at))
        line('total', {'event': 'close'}, {'backlog': -1, 'open': -1}, True, timestamp(final_at))

        # TODO review totals
        #for s in request.xpath('review/history/@when')
        for review in request.xpath('review[not(contains(@by_project, "{}:Staging:"))]'.format(args.project)):
            tags = {
                #'who_added': review.get('who'),
                'state': review.get('state'),
            }

            opened_at = date_parse(review.get('when'))
            history = review.find('history')
            if history is not None:
                #tags['completed'] = True
                #tags['state'] = review.get('state')
                completed_at = date_parse(history.get('when'))
                tags['who_completed'] = history.get('who')
            else:
                #tags['completed'] = False
                completed_at = final_at

            #count = 0
            tags['key'] = []
            for name, value in sorted(review.items()):
                if name.startswith('by_'):
                    tags[name] = value
                    #count += 1
                    tags['key'].append(value)
            #if count > 1:
            if 'by_project' in tags:
                if 'by_package' in tags:
                    tags['type'] = 'devel_package'
                else:
                    tags['type'] = 'devel'
            else:
                tags['type'] = 'group' if 'by_group' in tags else 'user'

            line('review', tags, {'open_for': (completed_at - opened_at).total_seconds()}, False, timestamp(completed_at))

            #if completed_at < opened_at:
                #print(completed_at)
            #print(completed_at)
            if tags['type'] == 'user' and tags['by_user'] == 'repo-checker':
                print(request_id)

            line('review_count', tags, {'count':  1}, True, timestamp(opened_at))
            line('review_count', tags, {'count': -1}, True, timestamp(completed_at))

            # TODO time spent in backlog (ie factory-staging)

        found = []
        for set_priority in request.xpath('history[description[contains(text(), "Request got a new priority:")]]'):
            parts = set_priority.find('description').text.rsplit(' ', 3)
            priority_previous = parts[1]
            priority = parts[3]
            #print(priority, set_priority.find('description').text)
            #print(priority, priority_previous)
            if priority == priority_previous:
                continue
            changed_at = date_parse(set_priority.get('when'))
            if priority_previous != 'moderate':
                line('priority', {'level': priority_previous}, {'count': -1}, True, timestamp(changed_at))
            if priority != 'moderate':
                line('priority', {'level': priority}, {'count': 1}, True, timestamp(changed_at))
                found.append(priority)

        priority = request.find('priority')
        if priority is not None and priority.text != 'moderate':
            if priority.text in found:
                line('priority', {'level': priority.text}, {'count': -1}, True, timestamp(final_at))
            else:
                print(request_id, priority.text)

        root = request
        #for review in root.xpath('review[@by_group="factory-staging" and @state="accepted"]'):
        number = 1
        for review in root.xpath('review[contains(@by_project, "{}:Staging:")]'.format(args.project)):
        #for review in root.xpath('review/*[history]'):
            history = review.find('history') # removed when parsed by request
            #print(review.get('when'))
            #print(review.get('by_project'))

            if not review.get('who'):
                #print(request_id)
                # TODO apparently a review can be in state="obsoleted" at which point
                # can only tell who staged by looking at previous accepted factory-staging
                # only 7 in all of Leap:42.3, but rather dumb
                # TODO also want who unstaged? to show who removed
                pass

            staged_at = date_parse(review.get('when'))

            project_type = 'adi' if api.is_adi_project(review.get('by_project')) else 'letter'
            short = api.extract_staging_short(review.get('by_project'))
            line('staging', {'id': short, 'type': project_type, 'event': 'select'}, {'count': 1}, True,
                 timestamp(staged_at))

            realdeal = request.xpath('history[@when="{}" and comment[contains(text(), "{}")]]/@who'.format(
                review.get('when'), review.get('by_project')))
            who = review.get('who')
            if len(realdeal):
                #print(who, realdeal[0])
                who = realdeal[0]
                swap_staged_user += 1
            else:
                __when = review.get('when')[:-2]
                #__when[] += int(__when[-2:]) + 1
                #print(review.get('when'), __when)
                realdeal = request.xpath('history[contains(@when, "{}") and comment[contains(text(), "{}")]]/@who'.format(
                __when, review.get('by_project')))
                if len(realdeal):
                    #print(who, realdeal[0])
                    who = realdeal[0]
                    swap_staged_user += 1
                else:
                    swap_staged_user_miss += 1
            #__tags = {'event': 'select', 'user': review.get('who'), 'number': number}
            __tags = {'event': 'select', 'user': who, 'number': number}
            __tags.update(request_tags)
            #if __tags['user'] == 'staging-bot' and __tags['whitelisted'] == True:
                #print(__tags)
            line('user', __tags, {'count': 1}, False,
                 timestamp(staged_at))

            line('total', {'event': 'select'}, {'backlog': -1, 'staged': 1}, True, timestamp(staged_at))

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

            line('total', {'event': 'unselect'}, {'backlog': 1, 'staged': -1}, True, timestamp(unselected_at))

    print('swap_staged_user', swap_staged_user)
    print('swap_staged_user_miss', swap_staged_user_miss)

    # Create starter line so all values are inherited.
    line('total', {}, {'backlog': 0, 'ignore': 0, 'open': 0, 'staged': 0},
                True, timestamp_earliest - 1)

    walk_lines(lines, args.project)

    points = []
    i = 0
    for line2 in sorted(lines, key=lambda l: l.timestamp):
        #if line2.measurement == 'total':
            #if i < 200:
                #print(line2)
            #i += 1
            #if line2.fields['open'] != (line2.fields['backlog'] + line2.fields['staged']):
                #print(line2)
            #if min(line2.fields.values()) < 0:
                #print(line2)

        points.append({
            'measurement': line2.measurement,
            'tags': line2.tags,
            'fields': line2.fields,
            'time': line2.timestamp,
            })

    db = args.project + 'v2'
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
    
    db = db[:-2]
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
