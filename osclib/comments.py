# Copyright (C) 2014 SUSE Linux Products GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

from datetime import datetime
import re
from xml.etree import cElementTree as ET

from osc.core import http_DELETE
from osc.core import http_GET
from osc.core import http_POST
from osc.core import makeurl


class CommentAPI(object):
    COMMENT_MARKER_REGEX = re.compile(r'<!-- (?P<bot>[^ ]+)(?P<info>(?: [^= ]+=[^ ]+)*) -->')
    LENGTH_MAX = 65535

    def __init__(self, apiurl):
        self.apiurl = apiurl

    def _prepare_url(self, request_id=None, project_name=None,
                     package_name=None, query=None):
        """Prepare the URL to get/put comments in OBS.

        :param request_id: Request where to refer the comment.
        :param project_name: Project name where to refer comment.
        :param package_name: Package name where to refer the comment.
        :returns: Formated URL for the request.
        """
        url = None
        if request_id:
            url = makeurl(self.apiurl, ['comments', 'request', request_id], query)
        elif project_name and package_name:
            url = makeurl(self.apiurl, ['comments', 'package', project_name,
                                        package_name], query)
        elif project_name:
            url = makeurl(self.apiurl, ['comments', 'project', project_name], query)
        else:
            raise ValueError('Please, set request_id, project_name or / and package_name to add a comment.')
        return url

    def _comment_as_dict(self, comment_element):
        """Convert an XML element comment into a dictionary.
        :param comment_element: XML element that store a comment.
        :returns: A Python dictionary object.
        """
        comment = {
            'who': comment_element.get('who'),
            'when': datetime.strptime(comment_element.get('when'), '%Y-%m-%d %H:%M:%S %Z'),
            'id': comment_element.get('id'),
            'parent': comment_element.get('parent', None),
            'comment': comment_element.text,
        }
        return comment

    def get_comments(self, request_id=None, project_name=None,
                     package_name=None):
        """Get the list of comments of an object in OBS.

        :param request_id: Request where to get comments.
        :param project_name: Project name where to get comments.
        :param package_name: Package name where to get comments.
        :returns: A list of comments (as a dictionary).
        """
        url = self._prepare_url(request_id, project_name, package_name)
        root = root = ET.parse(http_GET(url)).getroot()
        comments = {}
        for c in root.findall('comment'):
            c = self._comment_as_dict(c)
            comments[c['id']] = c
        return comments

    def comment_find(self, comments, bot, info_match=None):
        """Return previous bot comments that match criteria."""
        # Case-insensitive for backwards compatibility.
        bot = bot.lower()
        for c in comments.values():
            m = self.COMMENT_MARKER_REGEX.match(c['comment'])
            if m and bot == m.group('bot').lower():
                info = {}

                # Python base regex does not support repeated subgroup capture
                # so parse the optional info using string split.
                stripped = m.group('info').strip()
                if stripped:
                    for pair in stripped.split(' '):
                        key, value = pair.split('=')
                        info[key] = value

                # Skip if info does not match.
                if info_match:
                    match = True
                    for key, value in info_match.items():
                        if not(value is None or (key in info and info[key] == value)):
                            match = False
                            break
                    if not match:
                        continue

                return c, info
        return None, None

    def add_marker(self, comment, bot, info=None):
        """Add bot marker to comment that can be used to find comment."""

        if info:
            infos = []
            for key, value in info.items():
                infos.append('='.join((str(key), str(value))))

        marker = '<!-- {}{} -->'.format(bot, ' ' + ' '.join(infos) if info else '')
        return marker + '\n\n' + comment

    def add_comment(self, request_id=None, project_name=None,
                    package_name=None, comment=None, parent_id=None):
        """Add a comment in an object in OBS.

        :param request_id: Request where to write a comment.
        :param project_name: Project name where to write a comment.
        :param package_name: Package name where to write a comment.
        :param comment: Comment to be published.
        :return: Comment id.
        """
        if not comment:
            raise ValueError('Empty comment.')

        comment = comment.strip().encode('utf-8')
        if len(comment) > self.LENGTH_MAX:
            comment = self.truncate(comment)
        print(len(comment))

        query = {}
        if parent_id:
            query['parent_id'] = parent_id
        url = self._prepare_url(request_id, project_name, package_name, query)
        return http_POST(url, data=comment)

    def truncate(self, comment, suffix='...', length=65535):
        #print('truncate', len(comment), suffix, length)
        if length <= len(suffix) + len('\n</pre>'):
            return comment[:length]
        if len(comment) <= length:
            return comment

        snip = length - len(suffix)
        if comment.find('<pre>', 0, snip) != -1:
            # leave space for simplicity
            snip -= len('\n</pre>')
        #print('snip', snip)
        #  0, 5 NO
        # -1, 4
        # -2, 3
        # -3, 2
        # -4, 1
        # -5, 0 NO
        
        # what really after is did cursor land in the middle of a tag
        pre_last = max(comment.rfind('<pre>', snip - 4, snip + 4),
                       comment.rfind('</pre>', snip - 5, snip + 5))
        #print('pre_last', pre_last)
        if pre_last != -1:
        #if snip - pre_last <= 5:
        #if self.LENGTH_MAX - pre_last <= 4:
            snip = pre_last
        
        #pre_last = comment.rfind('<pre>', 0, snip + 4)
        #pre_last = max(comment.rfind('<pre>', 0, snip + 4),
                       #comment.rfind('</pre>', 0, snip + 5))
        ##print('pre_last', pre_last)
        #if snip - pre_last <= 5:
        ##if self.LENGTH_MAX - pre_last <= 4:
            #snip = pre_last
        
        comment = comment[:snip]
        
        # check if need to close a <pre>
        #print('<pre>', comment.count('<pre>'))
        #print('</pre>', comment.count('</pre>'))
        #if '</pre>' not in suffix and comment.count('<pre>') > comment.count('</pre>'):
        if comment.count('<pre>') > comment.count('</pre>'):
            suffix += '\n</pre>'
        
        return comment + suffix

    @staticmethod
    def find_last(comment, tag):
        return comment.rfind('<' + tag + '>', 0, snip + 4)

    def truncate_pre(self, tag):
        pre_last = comment.rfind('<' + tag + '>', 0, snip + 4)
        #print('pre_last', pre_last)
        if snip - pre_last <= 4:
        #if self.LENGTH_MAX - pre_last <= 4:
            snip = pre_last

    def truncate2(self, comment, suffix='...', length=65535):
        print('truncate', len(comment), suffix)
        #suffix = '...'
        #if comment.find('<pre>', end=self.LENGTH_MAX)
        #if comment.endswith('</pre>'):
            #suffix += '\n</pre>'
        snip = self.LENGTH_MAX - len(suffix)
        snip = length - len(suffix)
        print(snip)
        # Do no care at + 5 since then at the beginning
        # Whole idea is not to snip in the middle of a <pre> tag
        #pre_last = comment.rfind('<pre>', 0, self.LENGTH_MAX + 4)
        pre_last = comment.rfind('<pre>', 0, snip + 4)
        print('pre_last', pre_last)
        if length - pre_last <= 4:
        #if self.LENGTH_MAX - pre_last <= 4:
            snip = pre_last
        #comment = comment[:self.LENGTH_MAX - len(suffix)]
        print('snip', snip)
        original = comment
        comment = comment[:snip]
        
        # check if need to close a <pre>
        print('<pre>', comment.count('<pre>'))
        print('</pre>', comment.count('</pre>'))
        #if '</pre>' not in suffix and comment.count('<pre>') > comment.count('</pre>'):
        if comment.count('<pre>') > comment.count('</pre>'):
            #print(comment)
            #return self.truncate(original, suffix + '\n</pre>')
             #+ '\n</pre>'
            suffix2 = suffix + '\n</pre>'
            if len(comment) + len(suffix2) > length:
                return self.truncate(comment, suffix, length - len(suffix2))

        #if length - len(comment) > len(suffix):
        return comment + suffix

    def delete(self, comment_id):
        """Remove a comment object.
        :param comment_id: Id of the comment object.
        """
        url = makeurl(self.apiurl, ['comment', comment_id])
        return http_DELETE(url)

    def delete_children(self, comments):
        """Removes the comments that have no childs

        :param comments dict of id->comment dict
        :return same hash without the deleted comments
        """
        parents = []
        for comment in comments.values():
            if comment['parent']:
                parents.append(comment['parent'])

        for comment in comments.values():
            if comment['id'] not in parents:
                # Parent comments that have been removed are still returned
                # when children exist and are authored by _nobody_. Such
                # should not be deleted remotely, but only marked internally.
                if comment['who'] != '_nobody_':
                    self.delete(comment['id'])
                del comments[comment['id']]

        return comments

    def delete_from(self, request_id=None, project_name=None,
                    package_name=None):
        """Remove the comments related with a request, project or package.
        :param request_id: Request where to remove comments.
        :param project_name: Project name where to remove comments.
        :param package_name: Package name where to remove comments.
        :return: Number of comments removed.
        """
        comments = self.get_comments(request_id, project_name, package_name)
        while comments:
            comments = self.delete_children(comments)
        return True

    def delete_from_where_user(self, user, request_id=None, project_name=None,
                               package_name=None):
        """Remove comments where @user is mentioned.

        This method is used to remove notifications when a request is
        removed or moved to another project.
        :param user: User name where the comment will be removed.
        :param request_id: Request where to remove comments.
        :param project_name: Project name where to remove comments.
        :param package_name: Package name where to remove comments.
        :return: Number of comments removed.
        """
        for comment in self.get_comments(request_id, project_name, package_name).values():
            if comment['who'] == user:
                self.delete(comment['id'])
