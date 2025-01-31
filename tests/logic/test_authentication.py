# coding: utf-8
"""

"""

import pytest
from sampledb.logic import errors, users, authentication


@pytest.fixture
def user_id():
    user = users.create_user('test', 'user_id@example.com', users.UserType.PERSON)
    assert user.id is not None
    return user.id


def test_create_two_factor_authentication_method(user_id):
    method = authentication._create_two_factor_authentication_method(user_id, {'type': 'test'})
    assert method is not None
    assert method.user_id == user_id
    assert method.data == {'type': 'test'}
    assert not method.active


def test_get_two_factor_authentication_methods(user_id):
    methods = authentication.get_two_factor_authentication_methods(user_id)
    assert len(methods) == 0
    authentication._create_two_factor_authentication_method(user_id, {'type': 'test'})
    methods = authentication.get_two_factor_authentication_methods(user_id)
    assert len(methods) == 1
    method = methods[0]
    assert method is not None
    assert method.user_id == user_id
    assert method.data == {'type': 'test'}
    assert not method.active


def test_get_active_two_factor_authentication_method(user_id):
    assert authentication.get_active_two_factor_authentication_method(user_id) is None
    method = authentication._create_two_factor_authentication_method(user_id, {'type': 'test'})
    assert authentication.get_active_two_factor_authentication_method(user_id) is None
    authentication.activate_two_factor_authentication_method(method.id)
    assert authentication.get_active_two_factor_authentication_method(user_id).id == method.id


def test_activate_two_factor_authentication_method(user_id):
    method = authentication._create_two_factor_authentication_method(user_id, {'type': 'test'})
    assert not authentication.get_two_factor_authentication_methods(user_id)[0].active
    authentication.activate_two_factor_authentication_method(method.id)
    assert authentication.get_two_factor_authentication_methods(user_id)[0].active
    with pytest.raises(errors.TwoFactorAuthenticationMethodDoesNotExistError):
        authentication.activate_two_factor_authentication_method(method.id + 1)

    methods = authentication.get_two_factor_authentication_methods(user_id)
    assert [m.id for m in methods if m.active] == [method.id]
    other_method = authentication._create_two_factor_authentication_method(user_id, {'type': 'test'})
    authentication.activate_two_factor_authentication_method(other_method.id)
    methods = authentication.get_two_factor_authentication_methods(user_id)
    assert [m.id for m in methods if m.active] == [other_method.id]


def test_deactivate_two_factor_authentication_method(user_id):
    method = authentication._create_two_factor_authentication_method(user_id, {'type': 'test'})
    assert not authentication.get_two_factor_authentication_methods(user_id)[0].active
    authentication.activate_two_factor_authentication_method(method.id)
    assert authentication.get_two_factor_authentication_methods(user_id)[0].active
    authentication.deactivate_two_factor_authentication_method(method.id)
    assert not authentication.get_two_factor_authentication_methods(user_id)[0].active
    with pytest.raises(errors.TwoFactorAuthenticationMethodDoesNotExistError):
        authentication.deactivate_two_factor_authentication_method(method.id + 1)


def test_delete_two_factor_authentication_method(user_id):
    method_id = authentication._create_two_factor_authentication_method(user_id, {'type': 'test'}).id
    assert authentication.get_two_factor_authentication_methods(user_id)
    authentication.delete_two_factor_authentication_method(method_id)
    assert not authentication.get_two_factor_authentication_methods(user_id)
    with pytest.raises(errors.TwoFactorAuthenticationMethodDoesNotExistError):
        authentication.delete_two_factor_authentication_method(method_id)