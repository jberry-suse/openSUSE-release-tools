from osclib.util import project_list_family
from osclib.util import project_list_family_prior
from osclib.util import project_list_family_sorter
from osclib.util import project_list_prefix
from osclib import util
import unittest


class TestUtil(unittest.TestCase):
    def setUp(self):
        util._project_list_prefix = util.project_list_prefix
        def project_list_prefix_replacement(apiurl, prefix):
            if prefix == 'openSUSE:Leap':
                return [
                    'openSUSE:Leap:15.0',
                    'openSUSE:Leap:15.0:Update',
                    'openSUSE:Leap:15.0:NonFree',
                    'openSUSE:Leap:15.0:NonFree:Update',
                    'openSUSE:Leap:42.2',
                    'openSUSE:Leap:42.3',
                    'openSUSE:Leap:42.3:Update',
                    'openSUSE:Leap:42.3:NonFree',
                    'openSUSE:Leap:42.3:NonFree:Update',
                ]
            elif prefix == 'SUSE':
                return [
                    'SUSE:SLE-10',
                    'SUSE:SLE-10:SP2',
                    'SUSE:SLE-11',
                    'SUSE:SLE-11:GA',
                    'SUSE:SLE-11:SP1',
                    'SUSE:SLE-11:SP1:Update',
                    'SUSE:SLE-12:GA',
                    'SUSE:SLE-12-SP1:GA',
                    'SUSE:SLE-12-SP1:Update',
                    'SUSE:SLE-15:GA',
                ]

            return []

        util.project_list_prefix = project_list_prefix_replacement

    def tearDown(self):
        util.project_list_prefix = util._project_list_prefix

    def test_project_list_family(self):
        self.assertEqual(project_list_family(None, 'openSUSE:Factory'), ['openSUSE:Factory'])

        expected = ['openSUSE:Leap:15.0', 'openSUSE:Leap:42.2', 'openSUSE:Leap:42.3']
        self.assertEqual(expected, project_list_family(None, 'openSUSE:Leap:15.0'))
        self.assertEqual(expected, project_list_family(None, 'openSUSE:Leap:42.3'))

        expected = ['SUSE:SLE-12:GA', 'SUSE:SLE-12-SP1:GA', 'SUSE:SLE-15:GA']
        self.assertEqual(expected, project_list_family(None, 'SUSE:SLE-15:GA'))
        self.assertEqual(expected, project_list_family(None, 'SUSE:SLE-15-SP1:GA'))

    def test_project_list_family_sorter(self):
        projects = sorted(project_list_family(None, 'openSUSE:Leap:15.0'), key=project_list_family_sorter)
        self.assertEqual(projects[0], 'openSUSE:Leap:42.2')
        self.assertEqual(projects[2], 'openSUSE:Leap:15.0')

        projects = sorted(project_list_family(None, 'SUSE:SLE-15:GA'), key=project_list_family_sorter)
        self.assertEqual(projects[0], 'SUSE:SLE-12:GA')
        self.assertEqual(projects[2], 'SUSE:SLE-15:GA')

    def test_project_list_family_prior(self):
        projects = project_list_family_prior(None, 'openSUSE:Leap:15.0')
        self.assertEqual(projects, ['openSUSE:Leap:42.3', 'openSUSE:Leap:42.2'])

        projects = project_list_family_prior(None, 'openSUSE:Leap:42.3')
        self.assertEqual(projects, ['openSUSE:Leap:42.2'])

        projects = project_list_family_prior(None, 'SUSE:SLE-15:GA')
        self.assertEqual(projects, ['SUSE:SLE-12-SP1:GA', 'SUSE:SLE-12:GA'])

        projects = project_list_family_prior(None, 'SUSE:SLE-12-SP1:GA')
        self.assertEqual(projects, ['SUSE:SLE-12:GA'])
