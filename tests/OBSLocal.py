import os
from osc import conf
from osc.core import get_request
import subprocess
import unittest

OSCRC = os.path.expanduser('~/.oscrc-test')
APIURL = 'local-test'

class OBSLocalTestCase(unittest.TestCase):
    script = None
    script_apiurl = True
    script_debug = True
    script_debug_osc = True

    @classmethod
    def setUpClass(cls):
        # TODO #1214: Workaround for tests/obs.py's lack of cleanup.
        import httpretty
        httpretty.disable()

    def setUp(self):
        self.oscrc('Admin')
        conf.get_config(override_conffile=OSCRC,
                        override_no_keyring=True,
                        override_no_gnome_keyring=True)
        self.apiurl = conf.config['apiurl']


    @staticmethod
    def oscrc(userid):
        with open(OSCRC, 'w+') as f:
            f.write('\n'.join([
                '[general]',
                'apiurl = http://0.0.0.0:3000',
                '[http://0.0.0.0:3000]',
                'user={}'.format(userid),
                'pass=opensuse',
                'aliases={}'.format(APIURL),
                '',
            ]))

    def osc_user(self, userid):
        conf.config['api_host_options'][self.apiurl]['user'] = userid
        self.oscrc(userid)

    def execute(self, args):
        if self.script:
            args.insert(0, self.script)
        if self.script_debug:
            args.insert(1, '--debug')
        if self.script_debug_osc:
            args.insert(1, '--osc-debug')

        print('$ ' + ' '.join(args)) # Print command for debugging.
        try:
            self.output = subprocess.check_output(args, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            print(e.output)
            raise e
        print(self.output) # For debugging assertion failures.

    def assertOutput(self, string):
        self.assertTrue(string in self.output, '[MISSING] ' + string)

    def assertReview(self, rid, **kwargs):
        request = get_request(self.apiurl, rid)
        for review in request.reviews:
            for key, value in kwargs.items():
                if hasattr(review, key) and getattr(review, key) == value[0]:
                    self.assertEqual(review.state, value[1], '{}={} not {}'.format(key, value[0], value[1]))
                    return

        self.fail('{} not found'.format(kwargs))
