#!/usr/bin/python

import argparse
from datetime import datetime
from datetime import timedelta
import gzip
import io
from osc import conf
from osc.core import ET
from osc.core import search
from osc.core import xpath_join
from osclib.comments import CommentAPI
from osclib.conf import Config
from osclib.core import request_age
from osclib.memoize import memoize
import requests as r
import sys

def print_debug(message):
    if conf.config['debug']:
        print(message)

def request_debug(request, age, threshold):
    print_debug('{}: {} {} [{}]'.format(request.get('id'), age, threshold, age <= threshold))

@memoize(session=True)
def check_comment(apiurl, bot, **kwargs):
    if not len(kwargs):
        return False

    api = CommentAPI(apiurl)
    comments = api.get_comments(**kwargs)
    comment = api.comment_find(comments, bot)[0]
    if comment:
        return (datetime.utcnow() - comment['when']).total_seconds()

    return False

def check_announcer(project):
    Config(project)
    baseurl = conf.config[project].get('download-baseurl')

def check(apiurl, entity, entity_type='group', comment=False, bot=None,
          threshold=2 * 3600, threshold_require=True):
    if entity == 'announcer':
        print(Config('openSUSE:Factory').conf.config)
        return
        # TODO version and both target projects
        # TODO pontifex part
        #from factory_pacakge_news.announcer import config_defaults
        #'to': 'opensuse-factory@opensuse.org',
        now = datetime.utcnow()
        previous_month = now.replace(day=1) - timedelta(days=2)
        for month in (now, previous_month):
            #year = now.year
            #month = now.month
            url = 'https://lists.opensuse.org/{list}/{list}-{year}-{month}.mbox.gz'.format(
                list='opensuse-factory', year=month.year, month=month.strftime('%m'))
            print(url)
            response = r.get(url)
            with gzip.GzipFile(fileobj=io.BytesIO(response.content)) as handle:
                if 'New Tumbleweed snapshot {version} released!'.format(version='20171231') in handle.read():
                    print('yap')
            return

    queries = {'request': {'limit': 1000, 'withfullhistory': 1}}
    xpath = 'state[@name="new"] or state[@name="review"]'

    if entity == 'staging-bot':
        xpath = xpath_join(
            xpath, 'review[starts-with(@by_project, "openSUSE:") and @state="new"]', op='and')
        xpath = xpath_join(
            xpath, 'history/@who="{}"'.format(entity), op='and')

        requests = search(apiurl, queries, request=xpath)['request']
        for request in requests:
            age = request_age(request).total_seconds()
            request_debug(request, age, threshold)

            if age <= threshold:
                return True

        return False

    xpath = xpath_join(
        xpath, 'review[@by_{}="{}" and @state="new"]'.format(entity_type, entity), op='and')
    requests = search(apiurl, queries, request=xpath)['request']

    print_debug('{:,} requests'.format(len(requests)))
    if not len(requests):
        # Could check to see that a review has been performed in the last week.
        return True

    all_comment = True
    for request in requests:
        kwargs = {}
        if comment == 'project':
            # Would be a lot easier with lxml, but short of reparsing or monkey.
            for review in request.findall('review[@by_project]'):
                if review.get('by_project').startswith('openSUSE:'):
                    kwargs['project_name'] = review.get('by_project')
            # TODO repo-checker will miss stagings where delete only problem so
            # comment on request, but should be fixed by #1084.
        elif comment:
            kwargs['request_id'] = request.get('id')

        age = request_age(request).total_seconds()
        request_debug(request, age, threshold)
        comment_age = check_comment(apiurl, bot, **kwargs)
        if comment_age:
            if comment_age <= threshold:
                print_debug('comment found below threshold')
                return True
        elif age > threshold:
            print_debug('no comment found and above threshold')
            all_comment = False
            if threshold_require:
                return False
            else:
                continue
        else:
            print_debug('no comment found, but below threshold')

    print_debug('all comments: {}'.format(all_comment))
    return all_comment

def status(apiurl):
    # TODO If request ordering via api (openSUSE/open-build-service#4108) is
    # provided this can be implemented much more cleanly by looking for positive
    # activity (review changes) in threshold. Without sorting, some sampling of
    # all requests accepted are returned which is not useful.
    # TODO legal-auto, does not make comments so pending the above.
    bots = [
        ['announcer'],
        # No open requests older than 2 hours.
        #['factory-auto'],
        ## No open requests older than 2 hours or all old requests have comment.
        #['leaper', 'user', True, 'Leaper'],
        ## As long as some comment made in last 6 hours.
        #['repo-checker', 'user', 'project', 'RepoChecker', 6 * 3600, False],
        ## Different algorithm, any staging in last 24 hours.
        #['staging-bot', 'user', False, None, 24 * 3600],
    ]

    all_alive = True
    for bot in bots:
        result = check(apiurl, *bot)
        if not result:
            all_alive = False
        print('{} = {}'.format(bot[0], result))

    return all_alive

def main(args):
    #from calendar import Calendar
    #import calendar

    ##Calendar
    #print(calendar.monthrange(2018, 1))
    #now = datetime.utcnow()
    #print(now)
    #from datetime import timedelta
    #previous = now.replace(day=1) - timedelta(days=2)
    #print(now)
    #print(previous)
    #return
    
    conf.get_config(override_apiurl=args.apiurl)
    conf.config['debug'] = args.debug
    apiurl = conf.config['apiurl']
    return not status(apiurl)


if __name__ == '__main__':
    description = 'Check the status of the staging workflow bots.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', help='OBS instance API URL')
    parser.add_argument('-d', '--debug', action='store_true', help='print useful debugging info')
    parser.add_argument('-p', '--project', default='openSUSE:Factory', help='OBS project')
    args = parser.parse_args()

    sys.exit(main(args))
