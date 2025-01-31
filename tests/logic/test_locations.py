# coding: utf-8
"""

"""

import pytest

import sampledb
from sampledb.models import User, UserType, UserLogEntryType, Action, Object, ObjectLogEntryType
from sampledb.logic import locations, objects, actions, user_log, errors, object_log, components

UUID_1 = '28b8d3ca-fb5f-59d9-8090-bfdbd6d07a71'


@pytest.fixture
def component():
    component = components.add_component(address=None, uuid=UUID_1, name='Example component', description='')
    return component


@pytest.fixture
def user(app):
    with app.app_context():
        user = User(name='User', email="example@example.com", type=UserType.PERSON)
        sampledb.db.session.add(user)
        sampledb.db.session.commit()
        assert user.id is not None
    return user


@pytest.fixture
def action():
    action = actions.create_action(
        action_type_id=sampledb.models.ActionType.SAMPLE_CREATION,
        schema={
            'title': 'Example Object',
            'type': 'object',
            'properties': {
                'name': {
                    'title': 'Sample Name',
                    'type': 'text'
                }
            },
            'required': ['name']
        },
        instrument_id=None
    )
    return action


@pytest.fixture
def object(user: User, action: Action):
    data = {'name': {'_type': 'text', 'text': 'Object'}}
    return objects.create_object(user_id=user.id, action_id=action.id, data=data)


def test_create_location(user: User):
    assert len(locations.get_locations()) == 0
    locations.create_location({'en': "Example Location"}, {'en': "This is an example location"}, None, user.id)
    assert len(locations.get_locations()) == 1
    location = locations.get_locations()[0]
    assert location.name == {
        'en': "Example Location"
    }
    assert location.description == {
        'en': "This is an example location"
    }
    assert location.parent_location_id is None
    user_log_entries = user_log.get_user_log_entries(user.id)
    assert user_log_entries[-1].type == UserLogEntryType.CREATE_LOCATION
    assert user_log_entries[-1].data['location_id'] == location.id


def test_create_location_with_parent_location(user: User):
    parent_location = locations.create_location({'en': "Parent Location"}, {'en': "This is an example location"}, None, user.id)
    assert len(locations.get_locations()) == 1
    user_log_entries = user_log.get_user_log_entries(user.id)
    assert len(user_log_entries) == 1
    location = locations.create_location({'en': "Example Location"}, {'en': "This is an example location"}, parent_location.id, user.id)
    assert len(locations.get_locations()) == 2
    location = locations.get_location(location.id)
    assert location.name == {
        'en': "Example Location"
    }
    assert location.description == {
        'en': "This is an example location"
    }
    assert location.parent_location_id == parent_location.id
    user_log_entries = user_log.get_user_log_entries(user.id)
    assert len(user_log_entries) == 2
    user_log_entry = [e for e in user_log_entries if e.data.get('location_id', -1) == location.id][0]
    assert user_log_entry.type == UserLogEntryType.CREATE_LOCATION
    assert user_log_entry.data['location_id'] == location.id


def test_create_location_with_invalid_parent_location(user: User):
    parent_location = locations.create_location({'en': "Parent Location"}, {'en': "This is an example location"}, None, user.id)
    assert len(locations.get_locations()) == 1
    with pytest.raises(errors.LocationDoesNotExistError):
        locations.create_location({'en': "Example Location"}, {'en': "This is an example location"}, parent_location.id + 1, user.id)
    assert len(locations.get_locations()) == 1


def test_create_location_fed(user, component):
    assert len(locations.get_locations()) == 0
    locations.create_location({'en': "Example Location"}, {'en': "This is an example location"}, None, user.id, 1, component.id)
    assert len(locations.get_locations()) == 1
    location = locations.get_locations()[0]
    assert location.name == {'en': "Example Location"}
    assert location.description == {'en': "This is an example location"}
    assert location.parent_location_id is None
    assert len(user_log.get_user_log_entries(user.id)) == 0
    assert location.component.id == component.id
    assert location.fed_id == 1


def test_create_location_invalid_parameters(user, component):
    with pytest.raises(TypeError):
        locations.create_location(None, {'en': "This is an example location"}, None, user.id)
    with pytest.raises(TypeError):
        locations.create_location({'en': "Example Location"}, None, None, user.id)
    with pytest.raises(TypeError):
        locations.create_location({'en': "Example Location"}, {'en': "This is an example location"}, None, None)
    with pytest.raises(TypeError):
        locations.create_location({'en': "Example Location"}, {'en': "This is an example location"}, None, user.id, 1, None)
    with pytest.raises(TypeError):
        locations.create_location({'en': "Example Location"}, {'en': "This is an example location"}, None, user.id, None, component.id)


def test_create_location_fed_missing_component(user, component):
    with pytest.raises(errors.ComponentDoesNotExistError):
        locations.create_location({'en': "Example Location"}, {'en': "This is an example location"}, None, user.id, 1, component.id + 1)


def test_update_location(user: User):
    parent_location = locations.create_location({'en': "Parent Location"}, {'en': "This is an example location"}, None, user.id)
    location = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, None, user.id)
    assert len(locations.get_locations()) == 2
    locations.update_location(location.id, {'en': "Updated Location"}, {'en': "This is a location description"}, None, user.id)
    assert len(locations.get_locations()) == 2
    location = locations.get_location(location.id)
    assert location.name == {'en': "Updated Location"}
    assert location.description == {'en': "This is a location description"}
    assert location.parent_location_id is None
    locations.update_location(location.id, {'en': "Updated Location"}, {'en': "This is a location description"}, parent_location.id, user.id)
    location = locations.get_location(location.id)
    assert location.parent_location_id == parent_location.id
    user_log_entries = user_log.get_user_log_entries(user.id)
    assert [e for e in user_log_entries if e.data.get('location_id', -1) == location.id and e.type == UserLogEntryType.UPDATE_LOCATION]


def test_update_location_self_parent(user: User):
    location = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, None, user.id)
    with pytest.raises(errors.CyclicLocationError):
        locations.update_location(location.id, {'en': "Updated Location"}, {'en': "This is a location description"}, location.id, user.id)


def test_update_location_cyclic(user: User):
    parent_location = locations.create_location({'en': "Parent Location"}, {'en': "This is an example location"}, None, user.id)
    location = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, parent_location.id, user.id)
    with pytest.raises(errors.CyclicLocationError):
        locations.update_location(parent_location.id, {'en': "Parent Location"}, {'en': "This is an example location"}, location.id, user.id)


def test_update_location_parent_does_not_exist(user: User):
    location = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, None, user.id)
    with pytest.raises(errors.LocationDoesNotExistError):
        locations.update_location(location.id, {'en': "Location"}, {'en': "This is an example location"}, location.id + 1, user.id)


def test_update_location_which_does_not_exist(user: User):
    location = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, None, user.id)
    with pytest.raises(errors.LocationDoesNotExistError):
        locations.update_location(location.id + 1, {'en': "Location"}, {'en': "This is an example location"}, None, user.id)


def test_get_location_tree(user: User):
    child_location2 = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, None, user.id)
    parent_location = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, None, user.id)
    location = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, parent_location.id, user.id)
    child_location1 = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, location.id, user.id)
    locations.update_location(child_location2.id, {'en': "Location"}, {'en': "This is an example location"}, location.id, user.id)
    locations_map, locations_tree = locations.get_locations_tree()
    child_location2 = locations.get_location(child_location2.id)
    assert locations_map == {
        parent_location.id: parent_location,
        location.id: location,
        child_location1.id: child_location1,
        child_location2.id: child_location2
    }
    assert locations_tree == {
        parent_location.id: {
            location.id: {
                child_location1.id: {},
                child_location2.id: {}
            }
        }
    }


def test_assign_location(user: User, object: Object):
    object_location_assignment = locations.get_current_object_location_assignment(object.id)
    assert object_location_assignment is None
    location = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, None, user.id)
    locations.assign_location_to_object(object.id, location.id, None, user.id, {'en': "This object is stored at this location"})
    object_location_assignment = locations.get_current_object_location_assignment(object.id)
    assert object_location_assignment.object_id == object.id
    assert object_location_assignment.location_id == location.id
    assert object_location_assignment.user_id == user.id
    assert object_location_assignment.description == {'en': "This object is stored at this location"}
    user_log_entries = user_log.get_user_log_entries(user.id)
    assert [e for e in user_log_entries if e.data.get('object_location_assignment_id', -1) == object_location_assignment.id and e.type == UserLogEntryType.ASSIGN_LOCATION]
    object_log_entries = object_log.get_object_log_entries(object.id)
    assert [e for e in object_log_entries if e.data.get('object_location_assignment_id', -1) == object_location_assignment.id and e.type == ObjectLogEntryType.ASSIGN_LOCATION]


def test_assign_location_which_does_not_exist(user: User, object: Object):
    with pytest.raises(errors.LocationDoesNotExistError):
        locations.assign_location_to_object(object.id, 42, None, user.id, {'en': "This object is stored at this location"})
    object_location_assignment = locations.get_current_object_location_assignment(object.id)
    assert object_location_assignment is None


def test_assign_location_to_object_which_does_not_exist(user: User):
    location = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, None, user.id)
    with pytest.raises(errors.ObjectDoesNotExistError):
        locations.assign_location_to_object(42, location.id, None, user.id, {'en': "This object is stored at this location"})


def test_assign_location_multiple_times(user: User, object: Object):
    object_location_assignments = locations.get_object_location_assignments(object.id)
    assert object_location_assignments == []
    location1 = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, None, user.id)
    location2 = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, None, user.id)
    locations.assign_location_to_object(object.id, location1.id, None, user.id, {'en': "This object is stored at this location"})
    locations.assign_location_to_object(object.id, location2.id, None, user.id, {'en': "This object is stored at another location"})
    assert len(locations.get_object_location_assignments(object.id)) == 2
    object_location_assignment1, object_location_assignment2 = locations.get_object_location_assignments(object.id)
    assert object_location_assignment1.object_id == object.id
    assert object_location_assignment1.location_id == location1.id
    assert object_location_assignment1.user_id == user.id
    assert object_location_assignment1.description == {'en': "This object is stored at this location"}
    assert object_location_assignment2.object_id == object.id
    assert object_location_assignment2.location_id == location2.id
    assert object_location_assignment2.user_id == user.id
    assert object_location_assignment2.description == {'en': "This object is stored at another location"}


def test_object_ids_for_location(user: User, action: Action):
    data = {'name': {'_type': 'text', 'text': 'Object'}}
    object1 = objects.create_object(user_id=user.id, action_id=action.id, data=data)
    object2 = objects.create_object(user_id=user.id, action_id=action.id, data=data)
    object3 = objects.create_object(user_id=user.id, action_id=action.id, data=data)
    location1 = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, None, user.id)
    location2 = locations.create_location({'en': "Location"}, {'en': "This is an example location"}, None, user.id)
    assert locations.get_object_ids_at_location(location1.id) == set()
    assert locations.get_object_ids_at_location(location2.id) == set()
    locations.assign_location_to_object(object1.id, location1.id, None, user.id, {'en': ""})
    assert locations.get_object_ids_at_location(location1.id) == {object1.id}
    assert locations.get_object_ids_at_location(location2.id) == set()
    locations.assign_location_to_object(object2.id, location1.id, None, user.id, {'en': ""})
    assert locations.get_object_ids_at_location(location1.id) == {object1.id, object2.id}
    assert locations.get_object_ids_at_location(location2.id) == set()
    locations.assign_location_to_object(object3.id, location2.id, user.id, user.id, {'en': ""})
    assert locations.get_object_ids_at_location(location1.id) == {object1.id, object2.id}
    assert locations.get_object_ids_at_location(location2.id) == {object3.id}


def test_object_responsibility_confirmation(user: User, object: Object, app):
    other_user = User(name='Other User', email="example@example.com", type=UserType.PERSON)
    sampledb.db.session.add(other_user)
    sampledb.db.session.commit()
    server_name = app.config['SERVER_NAME']
    app.config['SERVER_NAME'] = 'localhost'
    with app.app_context():
        locations.assign_location_to_object(object.id, None, user.id, other_user.id, {'en': ""})
    app.config['SERVER_NAME'] = server_name
    object_location_assignment = locations.get_current_object_location_assignment(object.id)
    assert not object_location_assignment.confirmed
    locations.confirm_object_responsibility(object_location_assignment.id)
    object_location_assignment = locations.get_current_object_location_assignment(object.id)
    assert object_location_assignment.confirmed
    assert not object_location_assignment.declined
    with pytest.raises(errors.ObjectLocationAssignmentAlreadyConfirmedError):
        locations.decline_object_responsibility(object_location_assignment.id)
    object_location_assignment = locations.get_current_object_location_assignment(object.id)
    assert not object_location_assignment.declined
    assert object_location_assignment.confirmed


def test_object_responsibility_declination(user: User, object: Object, app):
    other_user = User(name='Other User', email="example@example.com", type=UserType.PERSON)
    sampledb.db.session.add(other_user)
    sampledb.db.session.commit()
    server_name = app.config['SERVER_NAME']
    app.config['SERVER_NAME'] = 'localhost'
    with app.app_context():
        locations.assign_location_to_object(object.id, None, user.id, other_user.id, {'en': ""})
    app.config['SERVER_NAME'] = server_name
    object_location_assignment = locations.get_current_object_location_assignment(object.id)
    assert not object_location_assignment.declined
    locations.decline_object_responsibility(object_location_assignment.id)
    object_location_assignment = locations.get_current_object_location_assignment(object.id)
    assert object_location_assignment.declined
    assert not object_location_assignment.confirmed
    with pytest.raises(errors.ObjectLocationAssignmentAlreadyDeclinedError):
        locations.confirm_object_responsibility(object_location_assignment.id)
    object_location_assignment = locations.get_current_object_location_assignment(object.id)
    assert object_location_assignment.declined
    assert not object_location_assignment.confirmed


def test_location_translations(user: User):
    location = locations.create_location(
        name={'en': "Example Location"},
        description={'en': "This is an example location"},
        parent_location_id=None,
        user_id=user.id
    )
    assert location.name == {
        'en': 'Example Location'
    }
    assert location.description == {
        'en': 'This is an example location'
    }

    with pytest.raises(Exception):
        locations.update_location(
            location_id=location.id,
            name="Example Location 2",
            description="This is an example location 2",
            parent_location_id=None,
            user_id=user.id
        )
    location = locations.get_location(location.id)
    assert location.name == {
        'en': 'Example Location'
    }
    assert location.description == {
        'en': 'This is an example location'
    }

    locations.update_location(
        location_id=location.id,
        name={'en': "Example Location 2"},
        description={'en': "This is an example location 2"},
        parent_location_id=None,
        user_id=user.id
    )
    location = locations.get_location(location.id)
    assert location.name == {
        'en': 'Example Location 2'
    }
    assert location.description == {
        'en': 'This is an example location 2'
    }

    german = sampledb.logic.languages.get_language_by_lang_code('de')
    sampledb.logic.languages.update_language(
        language_id=german.id,
        names=german.names,
        lang_code=german.lang_code,
        datetime_format_datetime=german.datetime_format_datetime,
        datetime_format_moment=german.datetime_format_moment,
        datetime_format_moment_output=german.datetime_format_moment_output,
        enabled_for_input=True,
        enabled_for_user_interface=True
    )

    locations.update_location(
        location_id=location.id,
        name={
            'en': "Example Location",
            'de': "Beispielort"
        },
        description={
            'en': "This is an example location",
            'de': "Dies ist ein Beispielort"
        },
        parent_location_id=None,
        user_id=user.id
    )
    location = locations.get_location(location.id)
    assert location.name == {
        'en': "Example Location",
        'de': "Beispielort"
    }
    assert location.description == {
        'en': "This is an example location",
        'de': "Dies ist ein Beispielort"
    }

    with pytest.raises(errors.LanguageDoesNotExistError):
        locations.update_location(
            location_id=location.id,
            name={
                'en': "Example Location",
                'xy': "Beispielort"
            },
            description={
                'en': "This is an example location",
                'xy': "Dies ist ein Beispielort"
            },
            parent_location_id=None,
            user_id=user.id
        )

    location = locations.get_location(location.id)
    assert location.name == {
        'en': "Example Location",
        'de': "Beispielort"
    }
    assert location.description == {
        'en': "This is an example location",
        'de': "Dies ist ein Beispielort"
    }

    with pytest.raises(errors.MissingEnglishTranslationError):
        locations.update_location(
            location_id=location.id,
            name={
                'de': "Beispielort"
            },
            description={
                'de': "Dies ist ein Beispielort"
            },
            parent_location_id=None,
            user_id=user.id
        )

    location = locations.get_location(location.id)
    assert location.name == {
        'en': "Example Location",
        'de': "Beispielort"
    }
    assert location.description == {
        'en': "This is an example location",
        'de': "Dies ist ein Beispielort"
    }

    with pytest.raises(errors.MissingEnglishTranslationError):
        locations.update_location(
            location_id=location.id,
            name={},
            description={},
            parent_location_id=None,
            user_id=user.id
        )

    location = locations.get_location(location.id)
    assert location.name == {
        'en': "Example Location",
        'de': "Beispielort"
    }
    assert location.description == {
        'en': "This is an example location",
        'de': "Dies ist ein Beispielort"
    }

    with pytest.raises(errors.LanguageDoesNotExistError):
        locations.create_location(
            name={
                'en': "Example Location 2",
                'xy': "Beispielort 2"
            },
            description={
                'en': "This is an example location",
                'xy': "Dies ist ein Beispielort"
            },
            parent_location_id=None,
            user_id=user.id
        )

    with pytest.raises(errors.MissingEnglishTranslationError):
        locations.create_location(
            name={
                'de': "Beispielort 2"
            },
            description={
                'de': "Dies ist ein Beispielort"
            },
            parent_location_id=None,
            user_id=user.id
        )

    with pytest.raises(errors.MissingEnglishTranslationError):
        locations.create_location(
            name={},
            description={},
            parent_location_id=None,
            user_id=user.id
        )


def test_get_location(user, component):
    location_created = locations.create_location(
        name={'en': "Example Location"},
        description={'en': "This is an example location"},
        parent_location_id=None,
        user_id=user.id
    )
    location = locations.get_location(location_created.id)
    assert location_created == location
    location_created = locations.create_location(
        name={'en': "Example Location"},
        description={'en': "This is an example location"},
        parent_location_id=None,
        user_id=user.id,
        fed_id=1,
        component_id=component.id
    )
    location = locations.get_location(1, component.id)
    assert location_created == location


def test_get_location_exceptions(component):
    with pytest.raises(errors.LocationDoesNotExistError):
        locations.get_location(42)
    with pytest.raises(errors.ComponentDoesNotExistError):
        locations.get_location(1, component.id + 1)
