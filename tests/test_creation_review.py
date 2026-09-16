from vea.semantic import SCHEMA, INSTRUCTIONS


def test_rubric_has_six_separate_checks_and_no_score():
    review = SCHEMA['properties']['creation_review']
    checks = review['properties']['checks']['properties']
    assert set(checks) == {'focus','argument','progression','audiovisual','editing','verification'}
    for check in checks.values():
        assert {'unknown', 'not_applicable'} <= set(check['properties']['status']['enum'])
        assert 'evidence' in check['required']
    assert 'score' not in str(review)


def test_all_objects_are_strict_and_all_properties_required():
    def visit(schema):
        if schema.get('type') == 'object':
            assert schema['additionalProperties'] is False
            assert set(schema['required']) == set(schema['properties'])
            for value in schema['properties'].values(): visit(value)
        if schema.get('type') == 'array': visit(schema['items'])
    visit(SCHEMA)
    assert 'You have not heard' in INSTRUCTIONS
    assert 'NEW PROPOSAL' in INSTRUCTIONS
