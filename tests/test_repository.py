"""Repository data boundaries and non-destructive initialization."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from content_policy import blocked_text, source_policy, validate_policy
from repo_check import allowed_path, check_content, check_index
from setup_local import setup


class RepositoryTest(unittest.TestCase):
    def test_private_and_generated_paths_are_never_source_paths(self):
        for name in ['.local/editor/posts/record/draft.json','content/records.json','content/record-media/a/photo.jpg',
                     'content/privacy.local.json','artifacts/manifest.json','dist/index.html','.env','.impeccable/mock.png',
                     'AGENTS.md','PRODUCT.md','SECURITY_REVIEW.md','deploy/routes.conf','deploy/yangarch.net.conf','assets/photo.jpg',
                     '../outside.py','scripts/__pycache__/example.py']:
            with self.subTest(name=name):self.assertFalse(allowed_path(name))
        for name in ['README.md','docs/DEVELOPMENT.md','scripts/editor.py','tests/test_records.py','assets/site.css',
                     '.github/workflows/ci.yml','content/records.example.json','content/privacy.example.json']:
            self.assertTrue(allowed_path(name))

    def test_fresh_clone_setup_preserves_existing_content_and_policy(self):
        with tempfile.TemporaryDirectory(prefix='repository-setup-') as temp:
            root=Path(temp)
            shutil.copytree(ROOT/'content',root/'content',ignore=shutil.ignore_patterns('records.json','record-media','privacy.local.json'))
            setup(root)
            self.assertEqual(json.loads((root/'content/records.json').read_text()),{'entries':[]})
            local=root/'content/records.json'
            local.write_text('{"entries":[],"local-marker":"keep"}')
            policy=root/'content/privacy.local.json'
            policy.write_text('{"blocked_terms":["local-only-example"]}')
            setup(root)
            self.assertIn('local-marker',local.read_text())
            self.assertIn('local-only-example',policy.read_text())
            self.assertTrue(blocked_text('LOCAL-ONLY-EXAMPLE',source_policy(root)))

    def test_forced_private_addition_is_rejected_from_the_index(self):
        with tempfile.TemporaryDirectory(prefix='repository-index-') as temp:
            root=Path(temp)
            subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'README.md').write_text('Safe project description\n')
            subprocess.run(['git','add','README.md'],cwd=root,check=True)
            check_index(root)
            (root/'content').mkdir()
            (root/'content/records.json').write_text('{"entries":[]}')
            subprocess.run(['git','add','-f','content/records.json'],cwd=root,check=True)
            with self.assertRaises(ValueError):check_index(root)

    def test_credentials_and_local_private_terms_fail_without_printing_values(self):
        policy=validate_policy({'blocked_terms':['local-only-example']})
        for raw in [('gh'+'p_'+'x'*36).encode(),b'Local-only-example',('/Users/'+'example-person/project').encode()]:
            with self.subTest(raw_length=len(raw)),self.assertRaises(ValueError) as result:
                check_content('README.md',raw,policy)
            self.assertNotIn(raw.decode(),str(result.exception))


if __name__=='__main__':unittest.main()
