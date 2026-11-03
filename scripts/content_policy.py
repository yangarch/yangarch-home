"""Keep private publication exclusions in local data, outside source control."""
import json

EXAMPLE_TERMS = ['unpublished-example-service','example-private-alias']


def validate_policy(data):
    if not isinstance(data,dict) or set(data)!={'blocked_terms'} or not isinstance(data['blocked_terms'],list):
        raise ValueError('Invalid content policy')
    if any(not isinstance(term,str) or not term.strip() or len(term)>200 for term in data['blocked_terms']):
        raise ValueError('Blocked terms must be nonempty text')
    return {'blocked_terms':sorted(set(EXAMPLE_TERMS+data['blocked_terms']))}


def source_policy(root):
    for name in ('privacy.local.json','privacy.example.json'):
        path=root/'content'/name
        if path.exists():
            if path.is_symlink():raise ValueError('Content policy cannot be a symlink')
            return validate_policy(json.loads(path.read_text()))
    return validate_policy({'blocked_terms':EXAMPLE_TERMS})


def release_policy(root):
    return validate_policy(json.loads((root/'artifacts/content-policy.json').read_text()))


def blocked_text(text, policy):
    lowered=text.casefold()
    return any(term.casefold() in lowered for term in policy['blocked_terms'])
