#!/usr/bin/python

from collections import namedtuple
import hashlib
import os
import pipes
import re
import subprocess
import sys
import tempfile

from osclib.core import binary_list
from osclib.core import BINARY_REGEX
from osclib.core import depends_on
from osclib.core import package_binary_list
from osclib.core import request_staged
from osclib.core import target_archs
from osclib.cycle import CycleDetector
from osclib.memoize import CACHEDIR

import ReviewBot

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
CheckResult = namedtuple('CheckResult', ('success', 'comment'))
INSTALL_REGEX = r"^(?:can't install (.*?)|found conflict of (.*?) with (.*?)):$"
InstallSection = namedtuple('InstallSection', ('binaries', 'text'))

class RepoChecker(ReviewBot.ReviewBot):
    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        # ReviewBot options.
        self.only_one_action = True
        self.request_default_return = True
        self.comment_handler = True

        # RepoChecker options.
        self.skip_cycle = False

    def project_only(self, project):
        # self.staging_config needed by target_archs().
        api = self.staging_api(project)

        comment = []
        for arch in self.target_archs(project):
            directory_project = self.mirror(project, arch)

            results = {
                'cycle': CheckResult(True, None),
                'install': self.install_check('', directory_project, arch, [], ['installation-images-debuginfodeps-Kubic']),
            }

            if not all(result.success for _, result in results.items()):
                self.result_comment(project, project, arch, results, comment)
                # TODO Check flag.
                # TODO Should also be merged so both archs together, but do
                # mapping per arch and file one issue per package
                #if not results['install'].success:
                    #self.foobar(project, arch, results['install'])

        text = '\n'.join(comment).strip()
        #print(text)
        #api.dashboard_content_ensure('repo_checker', text, 'project_only run')
        self.post_comment(project)

    def post_comment(self, project):
        for key, sections in self.package_results.items():
            #package = key
            print(key, len(sections))
            sections = sorted(sections, key=lambda s: s.text)
            print('\n'.join([section.text for section in sections]))
            message = '\n'.join([section.text for section in sections])
            message = '```\n' + message.strip() + '\n```'
            message = 'The version of this package in `{}` is uninstallable:\n\n'.format(project) + message

            binaries = set()
            for section in sections:
                binaries.update(section.binaries)
            info = ';'.join(['::'.join(sorted(binaries)), str(len(sections))])
            reference = hashlib.sha1(info).hexdigest()[:7]

            #for package in key:
            package = key
            _project, package = self.get_devel_project(project, package)
            print(_project, package)
            #message += "\n\n{}/{}".format(_project, package)
            #_project = 'home:jberry:repo-checker'
            #package = 'uninstallable-monster'
            self.comment_write(state='seen', result=reference,
                               project=_project, package=package, message=message)
            CABOOM()

    def foobar(self, project, arch, result_install):
        package_binaries, binary_map = package_binary_list(self.apiurl, project, 'standard', arch)

        # loop over file to parse into chunks and assign chunks to packages
        #sections = self.parse_install_output(result_install.comment)
        sections = self.parse_install_output(result_install)
        #grouped = {}
        for section in sections:
            print(section.binaries)
            # TODO lookup binaries
            packages = set([binary_map[b] for b in section.binaries])
            #key = '::'.join(packages)
            #key = packages
            #grouped.setdefault(key, [])
            #grouped[key].append(section)
            for package in packages:
                # make note about combining
                key = package
                self.package_results.setdefault(key, [])
                self.package_results[key].append(section)

        #return grouped

        #for key, sections in grouped.items():
            #print(key, len(sections))
            #print('\n'.join([section.text for section in sections]))

        #self.comment_write(state='seen', result='failed', project=group,
                #message='\n'.join(comment).strip(), identical=True)

    def parse_install_output(self, output):
        section = None
        text = None

        for line in output.splitlines(True):
            if line.startswith(' '):
                if section:
                    text += line
            else:
                if section:
                    yield InstallSection(section, text)

                match = re.match(INSTALL_REGEX, line)
                if match:
                    binaries = [b for b in match.groups() if b is not None]
                    section = binaries
                    text = line
                else:
                    section = None

        if section:
            yield InstallSection(section, text)

    #def parse_install_output(self, output):
        #sections = []
        #section = None

        #for line in output.splitlines(True):
            #if line.startswith(' '):
                #if section:
                    #section.text += line
            #else:
                #match = re.match(INSTALL_REGEX, line)
                #if match:
                    #binaries = [p for p in match.groups() if p is not None]
                    ##if match.group(1):
                        ##binaries = [match.group(1)]
                    ##else:
                        ##binaries = [match.group(2), match.group(3)]
                    #section = InstallSection(binaries, line)
                    #sections.append(section)
                #else:
                    #section = None

        #return sections
        #for match in re.finditer(INSTALL_REGEX, result_install.comment):
            #if match.group(1):
                
        #BINARY_REGEX

    def prepare_review(self):
        # Reset for request batch.
        self.requests_map = {}
        self.groups = {}

        # Manipulated in ensure_group().
        self.group = None
        self.mirrored = set()

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

        if group == self.group:
            # Only process a group the first time it is encountered.
            return self.group_pass

        self.logger.info('group {}'.format(group))
        self.group = group
        self.group_pass = True

        comment = []
        for arch in self.target_archs(project):
            if arch not in self.target_archs(group):
                self.logger.debug('{}/{} not available'.format(group, arch))
                continue

            # Mirror both projects the first time each are encountered.
            directory_project = self.mirror(project, arch)
            directory_group = self.mirror(group, arch)

            # Generate list of rpms to ignore from the project consisting of all
            # packages in group and those that were deleted.
            ignore = set()
            self.ignore_from_repo(directory_group, ignore)

            for r in self.groups[group]:
                a = r.actions[0]
                if a.type == 'delete':
                    self.ignore_from_package(project, a.tgt_package, arch, ignore)

            whitelist = self.package_whitelist(project, arch)

            # Perform checks on group.
            results = {
                'cycle': self.cycle_check(project, group, arch),
                'install': self.install_check(directory_project, directory_group, arch, ignore, whitelist),
            }

            if not all(result.success for _, result in results.items()):
                # Not all checks passed, build comment.
                self.group_pass = False
                self.result_comment(project, group, arch, results, comment)

        if not self.group_pass:
            # Some checks in group did not pass, post comment.
            self.comment_write(state='seen', result='failed', project=group,
                               message='\n'.join(comment).strip(), identical=True)

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
        if (project, arch) in self.mirrored or True:
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
        for binary in binary_list(self.apiurl, project, 'standard', arch, package):
            ignore.add(binary.name)

        return ignore

    def package_whitelist(self, project, arch):
        product = project.split(':Staging:', 1)[0]
        prefix = 'repo_checker-package-whitelist'
        whitelist = set()
        for key in [prefix, '-'.join([prefix, arch])]:
            whitelist.update(self.staging_config[product].get(key, '').split(' '))
        return whitelist

    def install_check(self, directory_project, directory_group, arch, ignore, whitelist):
        self.logger.info('install check: start')

        with tempfile.NamedTemporaryFile() as ignore_file:
            # Print ignored rpms on separate lines in ignore file.
            for item in ignore:
                ignore_file.write(item + '\n')
            ignore_file.flush()

            # Invoke repo_checker.pl to perform an install check.
            script = os.path.join(SCRIPT_PATH, 'repo_checker.pl')
            parts = ['LC_ALL=C', 'perl', script, arch, directory_group,
                     '-r', directory_project, '-f', ignore_file.name,
                     '-w', ','.join(whitelist)]
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
            else:
                # TODO parse()
                #self.foobar(project, arch, stdout)
                self.foobar('openSUSE:Factory', arch, stdout)
                #pass

            # Format output as markdown comment.
            code = '```\n'
            parts = []

            stdout = stdout.strip()
            if stdout:
                parts.append(code + stdout + '\n' + code)
            stderr = stderr.strip()
            if stderr:
                parts.append(code + stderr + '\n' + code)

            return CheckResult(False, ('\n' + ('-' * 80) + '\n\n').join(parts))


        self.logger.info('install check: passed')
        return CheckResult(True, None)

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
        comment.append('## ' + arch + '\n')
        if not results['cycle'].success:
            comment.append('### new [cycle(s)](/project/repository_state/{}/standard)\n'.format(group))
            comment.append(results['cycle'].comment + '\n')
        if not results['install'].success:
            comment.append('### [install check & file conflicts](/package/view_file/{}:Staging/dashboard/repo_checker)\n'.format(project))
            comment.append(results['install'].comment + '\n')

    def check_action_submit(self, request, action):
        if not self.ensure_group(request, action):
            return None

        self.review_messages['accepted'] = 'cycle and install check passed'
        return True

    def check_action_delete(self, request, action):
        # TODO Include runtime dependencies instead of just build dependencies.
        # TODO Ignore tgt_project packages that depend on this that are part of
        # ignore list as and instead look at output from staging for those.
        what_depends_on = depends_on(self.apiurl, action.tgt_project, 'standard', [action.tgt_package], True)
        if len(what_depends_on):
            self.logger.warn('{} still required by {}'.format(action.tgt_package, ', '.join(what_depends_on)))

        if len(self.comment_handler.lines):
            self.comment_write(result='decline')
            return False

        # Allow for delete to be declined before ensuring group passed.
        if not self.ensure_group(request, action):
            return None

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

    def do_project_only(self, subcmd, opts, project):
        self.checker.check_requests() # Needed to properly init ReviewBot.
        self.checker.project_only(project)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
