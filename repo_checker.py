#!/usr/bin/python

import cmdln
from collections import namedtuple
import hashlib
import os
import pipes
import re
import subprocess
import sys
import tempfile

from osclib.core import binary_list
from osclib.core import depends_on
from osclib.core import package_binary_list
from osclib.core import package_list
from osclib.core import request_staged
from osclib.core import target_archs
from osclib.cycle import CycleDetector
from osclib.memoize import CACHEDIR

import ReviewBot

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
CheckResult = namedtuple('CheckResult', ('success', 'comment'))
INSTALL_REGEX = r"^(?:can't install (.*?)|found conflict of (.*?) with (.*?)):$"
InstallSection = namedtuple('InstallSection', ('binaries', 'text'))

def utf8_lead_byte(b):
    '''A UTF-8 intermediate byte starts with the bits 10xxxxxx.'''
    return (ord(b) & 0xC0) != 0x80

def utf8_byte_truncate(text, max_bytes):
    '''If text[max_bytes] is not a lead byte, back up until a lead byte is
    found and truncate before that character.'''
    utf8 = text.encode('utf-8')
    if len(utf8) <= max_bytes:
        return utf8
    i = max_bytes
    print('max_bytes', max_bytes)
    while i > 0 and not utf8_lead_byte(utf8[i]):
        i -= 1
    print('i', i)
    return utf8[:i]

def unicode_truncate(s, length, encoding='utf-8'):
    encoded = s.encode(encoding)[:length]
    return encoded.decode(encoding, 'ignore')

class RepoChecker(ReviewBot.ReviewBot):
    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        # ReviewBot options.
        self.only_one_action = True
        self.request_default_return = True
        self.comment_handler = True

        # RepoChecker options.
        self.skip_cycle = False

    def project_only(self, project, post_comments=False):
        # self.staging_config needed by target_archs().
        api = self.staging_api(project)

        comment = []
        for arch in self.target_archs(project):
            directory_project = self.mirror(project, arch)

            parse = project if post_comments else False
            results = {
                'cycle': CheckResult(True, None),
                'install': self.install_check('', directory_project, '', arch, [], [], parse=parse),
            }

            if not all(result.success for _, result in results.items()):
                self.result_comment(project, project, arch, results, comment)

        text = '\n'.join(comment).strip()
        api.dashboard_content_ensure('repo_checker', text, 'project_only run')

        if post_comments:
            self.package_comments(project)

    def package_comments(self, project):
        self.logger.info('{} package comments'.format(len(self.package_results)))

        for package, sections in self.package_results.items():
            template = 'The version of this package in `{}` has installation issues and may not be installable:\n\n<pre>\n'.format(project) # {}\n</pre>

            # Sort sections by text to group binaries together.
            #space_remaining = 65535 - len(template) # has the {} in it
            space_remaining = 65535 - len(template) - 1500 # has the {} in it
            sections = sorted(sections, key=lambda s: s.text)
            message = '\n'.join([section.text for section in sections]).strip()
            #if len(message) > space_remaining:
            if sys.getsizeof(message) > space_remaining * 2:
                # Truncate messages to avoid crashing OBS.
                #message = message[:space_remaining - 3] + '...'
                #message = utf8_byte_truncate(message, (space_remaining - 3) * 2) + '...'
                message = unicode_truncate(message, space_remaining - 3) + '...'
            message = template + message + '\n</pre>'
            #print(sys.getsizeof(message))

            # Generate a hash based on the binaries involved and the number of
            # sections. This eliminates version or release changes from causing
            # an update to the comment while still updating on relevant changes.
            binaries = set()
            for section in sections:
                binaries.update(section.binaries)
            info = ';'.join(['::'.join(sorted(binaries)), str(len(sections))])
            reference = hashlib.sha1(info).hexdigest()[:7]

            # Post comment on devel package in order to notifiy maintainers.
            devel_project, devel_package = self.get_devel_project(project, package)
            self.comment_write(state='seen', result=reference,
                               project=devel_project, package=devel_package, message=message)

    def prepare_review(self):
        # Reset for request batch.
        self.requests_map = {}
        self.groups = {}

        # Manipulated in ensure_group().
        self.group = None
        self.mirrored = set()

        # Stores parsed install_check() results grouped by package.
        self.package_results = {}

        # Look for requests of interest and group by staging.
        for request in self.requests:
            # Only interesting if request is staged.
            group = request_staged(request)
            if not group:
                self.logger.debug('{}: not staged'.format(request.reqid))
                continue

            # Only interested if group has completed building.
            api = self.staging_api(request.actions[0].tgt_project)
            status = api.project_status(group, True)
            # Corrupted requests may reference non-existent projects and will
            # thus return a None status which should be considered not ready.
            if not status or str(status['overall_state']) not in ('testing', 'review', 'acceptable'):
                self.logger.debug('{}: {} not ready'.format(request.reqid, group))
                continue

            # Only interested if request is in consistent state.
            selected = api.project_status_requests('selected')
            if request.reqid not in selected:
                self.logger.debug('{}: inconsistent state'.format(request.reqid))

            self.requests_map[int(request.reqid)] = group

            requests = self.groups.get(group, [])
            requests.append(request)
            self.groups[group] = requests

            self.logger.debug('{}: {} ready'.format(request.reqid, group))

        # Filter out undesirable requests and ensure requests are ordered
        # together with group for efficiency.
        count_before = len(self.requests)
        self.requests = []
        for group, requests in sorted(self.groups.items()):
            self.requests.extend(requests)

        self.logger.debug('requests: {} skipped, {} queued'.format(
            count_before - len(self.requests), len(self.requests)))

    def ensure_group(self, request, action):
        project = action.tgt_project
        group = self.requests_map[int(request.reqid)]
        if re.match(r'.*?:Staging:[A-Z]$', group):
            group_sub = group + ':DVD'
        else:
            group_sub = False

        if group == self.group:
            # Only process a group the first time it is encountered.
            return self.group_pass

        self.logger.info('group {}'.format(group))
        self.group = group
        self.group_sub = group_sub # TODO yikes
        self.group_pass = True

        comment = []
        for arch in self.target_archs(project):
            if arch not in self.target_archs(group):
                self.logger.debug('{}/{} not available'.format(group, arch))
                continue
            if group_sub and arch in self.target_archs(group_sub):
                group_sub_do = True
            else:
                group_sub_do = False
            #print(group, group_sub, group_sub_do, arch, self.target_archs(group_sub))

            # Mirror both projects the first time each are encountered.
            directory_project = self.mirror(project, arch)
            directory_group = self.mirror(group, arch)
            if group_sub_do:
                directory_group_sub = self.mirror(group_sub, arch)
            else:
                directory_group_sub = ''

            # Generate list of rpms to ignore from the project consisting of all
            # packages in group and those that were deleted.
            ignore = set()

            #packages = package_list(self.apiurl, group)
            self.ignore_from_package_list(project, group, arch, ignore)
            #for package in packages:
                #self.ignore_from_package(project, a.tgt_package, arch, ignore)

            if group_sub_do:
            #if group_sub_do and False:
                #packages = package_list(self.apiurl, group_sub)
                self.ignore_from_package_list(project, group_sub, arch, ignore)

            #if re.match(r'.*?:Staging:[A-Z]$', group):
                #packages = package_list(self.apiurl, group + ':DVD')
                #self.ignore_from_package_list(project, packages, arch, ignore)
                #for package in packages:
                    #self.ignore_from_package(project, a.tgt_package, arch, ignore)

            #sys.exit()
            #self.ignore_from_repo(directory_group, ignore)
            #if re.match(r'.*?:Staging:[A-Z]$', group):
                #directory = os.path.join(CACHEDIR, group + ':DVD', 'standard', arch)
                #self.ignore_from_repo(directory, ignore)

            #for r in self.groups[group]:
                #a = r.actions[0]
                #if a.type == 'delete':
                    #self.ignore_from_package(project, a.tgt_package, arch, ignore)

            #print(ignore)
            #print(len(ignore))
            #for i in ignore:
                #print(i)

            #ignore2 = set()
            #self.ignore_from_repo(directory_group, ignore2)
            #print(len(ignore2))
            #a = 0
            #for i in ignore2:
                #print(i)
                #a += 1
                #if a >= 100:
                    #break

            whitelist = self.package_whitelist(project, arch)

            # Perform checks on group.
            results = {
                'cycle': self.cycle_check(project, group, arch),
                'install': self.install_check(directory_project, directory_group, directory_group_sub, arch, ignore, whitelist),
            }

            if not all(result.success for _, result in results.items()):
                # Not all checks passed, build comment.
                self.group_pass = False
                self.result_comment(project, group, arch, results, comment)

        if not self.group_pass:
            text = ''
            #length = 0
            max_length = 65535 - 1000
            #max_length = 65535
            for line in comment:
                text += line + '\n'
                #length += len(line) + 1
                #print(len(text), sys.getsizeof(text))

                #if length > max_length:
                if sys.getsizeof(text) > max_length * 2:
                    #print(text[-50:])
                    if text.strip().endswith('</pre>'):
                        # Truncate comments to avoid crashing OBS.
                        #text = text[:max_length - 10] + '...\n</pre>'
                        #text = utf8_byte_truncate(text, (max_length - 10) * 2) + '...\n</pre>'
                        text = unicode_truncate(text, max_length - 10) + '...\n</pre>'
                        #text = utf8_byte_truncate(text, (max_length - 10) * 2)
                    else:
                        #text = text[:max_length - 3] + '...'
                        #text = utf8_byte_truncate(text, (max_length - 3) * 2) + '...'
                        text = unicode_truncate(text, max_length - 3) + '...'
                    break
            #print(sys.getsizeof(text))
            #print(sys.getsizeof('...\n</pre>'))
            #print(sys.getsizeof('...\n</pre>'.encode('utf-8')))
            #print(len('...\n</pre>'.encode('utf-8')))
            #131070
            #131050

            # Some checks in group did not pass, post comment.
            self.comment_write(state='seen', result='failed', project=group,
                               #message='\n'.join(comment).strip(), identical=True)
                               message=text.strip(), identical=True)
        else:
            text = 'Previously reported problems have been resolved.'
            self.comment_write(state='done', result='passed', project=group,
                               message=text.strip(), identical=True, only_replace=True)

        return self.group_pass

    def target_archs(self, project):
        archs = target_archs(self.apiurl, project)

        # Check for arch whitelist and use intersection.
        product = project.split(':Staging:', 1)[0]
        whitelist = self.staging_config[product].get('repo_checker-arch-whitelist')
        if whitelist:
            archs = list(set(whitelist.split(' ')).intersection(set(archs)))

        # Trick to prioritize x86_64.
        return sorted(archs, reverse=True)

    def mirror(self, project, arch):
        """Call bs_mirrorfull script to mirror packages."""
        directory = os.path.join(CACHEDIR, project, 'standard', arch)
        if (project, arch) in self.mirrored:
            # Only mirror once per request batch.
            return directory

        if not os.path.exists(directory):
            os.makedirs(directory)

        script = os.path.join(SCRIPT_PATH, 'bs_mirrorfull')
        path = '/'.join((project, 'standard', arch))
        url = '{}/public/build/{}'.format(self.apiurl, path)
        parts = ['LC_ALL=C', 'perl', script, '--nodebug', url, directory]
        parts = [pipes.quote(part) for part in parts]

        self.logger.info('mirroring {}'.format(path))
        if os.system(' '.join(parts)):
             raise Exception('failed to mirror {}'.format(path))

        self.mirrored.add((project, arch))
        return directory

    def ignore_from_repo(self, directory, ignore):
        """Extract rpm names from mirrored repo directory."""
        for filename in os.listdir(directory):
            if not filename.endswith('.rpm'):
                continue
            _, basename = filename.split('-', 1)
            ignore.add(basename[:-4])

    def ignore_from_package(self, project, package, arch, ignore):
        """Extract rpm names from current build of package."""
        try:
            # TODO Perhaps use package_binary_list() to avoid lots of queries.
            for binary in binary_list(self.apiurl, project, 'standard', arch, package):
                ignore.add(binary.name)
        except HTTPError as e:
            # Ignore package not found new package submissions.
            if e.code != 404:
                raise e

    def ignore_from_package_list(self, project, group, arch, ignore):
        """Extract rpm names from current build of package."""
        _, binary_map = package_binary_list(self.apiurl, group, 'standard', arch)
        packages = set(binary_map.values())
        #print(len(packages))

        binaries, _ = package_binary_list(self.apiurl, project, 'standard', arch)
        for binary in binaries:
            if binary.package in packages:
                #print(binary.filename, binary.name)
                ignore.add(binary.name)

    def package_whitelist(self, project, arch):
        prefix = 'repo_checker-package-whitelist'
        whitelist = set()
        for key in [prefix, '-'.join([prefix, arch])]:
            whitelist.update(self.staging_config[project].get(key, '').split(' '))
        return whitelist

    def install_check(self, directory_project, directory_group, directory_group_sub, arch, ignore, whitelist, parse=False):
        self.logger.info('install check: start')

        with tempfile.NamedTemporaryFile() as ignore_file:
            # Print ignored rpms on separate lines in ignore file.
            for item in ignore:
                ignore_file.write(item + '\n')
            ignore_file.flush()

            # Invoke repo_checker.pl to perform an install check.
            script = os.path.join(SCRIPT_PATH, 'repo_checker.pl')
            #directory_group_sub = ''
            parts = ['LC_ALL=C', 'perl', script, arch, directory_group,
                     '-r', directory_project, '-f', ignore_file.name,
                     '-s', directory_group_sub, '-w', ','.join(whitelist)]
            parts = [pipes.quote(part) for part in parts]
            p = subprocess.Popen(' '.join(parts), shell=True,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, close_fds=True)
            stdout, stderr = p.communicate()

        if p.returncode:
            self.logger.info('install check: failed')
            if p.returncode == 126:
                self.logger.warn('mirror cache reset due to corruption')
                self.mirrored = set()
            elif parse:
                # Parse output for later consumption for posting comments.
                sections = self.install_check_parse(stdout)
                self.install_check_sections_group(parse, arch, sections)

            # Format output as markdown comment.
            #code = '```\n'
            parts = []

            stdout = stdout.strip()
            if stdout:
                parts.append('<pre>\n' + stdout + '\n' + '</pre>\n')
            stderr = stderr.strip()
            if stderr:
                parts.append('<pre>\n' + stderr + '\n' + '</pre>\n')

            return CheckResult(False, ('\n' + ('-' * 80) + '\n\n').join(parts))


        self.logger.info('install check: passed')
        return CheckResult(True, None)

    def install_check_sections_group(self, project, arch, sections):
        _, binary_map = package_binary_list(self.apiurl, project, 'standard', arch)

        for section in sections:
            # If switch to creating bugs likely makes sense to join packages to
            # form grouping key and create shared bugs for conflicts.
            # Added check for b in binary_map after encountering:
            # https://lists.opensuse.org/opensuse-buildservice/2017-08/msg00035.html
            # Under normal circumstances this should never occur.
            packages = set([binary_map[b] for b in section.binaries if b in binary_map])
            for package in packages:
                self.package_results.setdefault(package, [])
                self.package_results[package].append(section)

    def install_check_parse(self, output):
        section = None
        text = None

        # Loop over lines and parse into chunks assigned to binaries.
        for line in output.splitlines(True):
            if line.startswith(' '):
                if section:
                    text += line
            else:
                if section:
                    yield InstallSection(section, text)

                match = re.match(INSTALL_REGEX, line)
                if match:
                    # Remove empty groups since regex matches different patterns.
                    binaries = [b for b in match.groups() if b is not None]
                    section = binaries
                    text = line
                else:
                    section = None

        if section:
            yield InstallSection(section, text)

    def cycle_check(self, project, group, arch):
        if self.skip_cycle:
            self.logger.info('cycle check: skip due to --skip-cycle')
            return CheckResult(True, None)

        self.logger.info('cycle check: start')
        cycle_detector = CycleDetector(self.staging_api(project))
        comment = []
        for index, (cycle, new_edges, new_packages) in enumerate(
            cycle_detector.cycles(group, arch=arch), start=1):
            if new_packages:
                # New package involved in cycle, build comment.
                comment.append('- #{}: {} package cycle, {} new edges'.format(
                    index, len(cycle), len(new_edges)))

                comment.append('   - cycle')
                for package in sorted(cycle):
                    comment.append('      - {}'.format(package))

                comment.append('   - new edges')
                for edge in sorted(new_edges):
                    comment.append('      - ({}, {})'.format(edge[0], edge[1]))

        if len(comment):
            # New cycles, post comment.
            self.logger.info('cycle check: failed')
            return CheckResult(False, '\n'.join(comment))

        self.logger.info('cycle check: passed')
        return CheckResult(True, None)

    def result_comment(self, project, group, arch, results, comment):
        """Generate comment from results"""
        #if len(comment) > 65535:
            #return
        comment.append('## ' + arch + '\n')
        if not results['cycle'].success:
            comment.append('### new [cycle(s)](/project/repository_state/{}/standard)\n'.format(group))
            comment.append(results['cycle'].comment + '\n')
        if not results['install'].success:
            comment.append('### [install check & file conflicts](/package/view_file/{}:Staging/dashboard/repo_checker)\n'.format(project))
            comment.append(results['install'].comment + '\n')
        #if len(comment) > 65535:
            ## Truncate comments to avoid crashing OBS.
            #comment = comment[:65535 - 7] + '...\n```'

    def check_action_submit(self, request, action):
        if not self.ensure_group(request, action):
            return None

        self.review_messages['accepted'] = 'cycle and install check passed'
        return True

    def check_action_delete(self, request, action):
        # Allow for delete to be declined before ensuring group passed. <-- nope
        if not self.ensure_group(request, action):
            return None

        # TODO Include runtime dependencies instead of just build dependencies.
        # TODO Ignore tgt_project packages that depend on this that are part of
        # ignore list as and instead look at output from staging for those.
        what_depends_on = depends_on(self.apiurl, action.tgt_project, 'standard', [action.tgt_package], True)
        # TODO make sure not a package in staging, this should be after grouped
        if len(what_depends_on):
            # need group (plus subs or general way to handle)
            # find packages staged with it and want depends not including those
            packages = set(package_list(self.apiurl, self.group))
            if self.group_sub:
                packages.update(package_list(self.apiurl, self.group_sub))
            print(what_depends_on, what_depends_on - packages)
            if len(what_depends_on - packages):
                #self.logger.warn('{} still required by {}'.format(action.tgt_package, ', '.join(what_depends_on)))
                self.comment_write(state='seen', result='failed', identical=True,
                    message='{} still required by {}'.format(action.tgt_package, ', '.join(what_depends_on)))
                return None

        #if len(self.comment_handler.lines):
            #self.comment_write(result='decline')
            #return False
            #self.comment_write(state='seen', result='failed', message=, identical=True)
            #return None

        self.review_messages['accepted'] = 'delete request is safe'
        return True


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = RepoChecker

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)

        parser.add_option('--skip-cycle', action='store_true', help='skip cycle check')

        return parser

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        if self.options.skip_cycle:
            bot.skip_cycle = self.options.skip_cycle

        return bot

    @cmdln.option('--post-comments', action='store_true', help='post comments to packages with issues')
    def do_project_only(self, subcmd, opts, project):
        self.checker.check_requests() # Needed to properly init ReviewBot.
        self.checker.project_only(project, opts.post_comments)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
