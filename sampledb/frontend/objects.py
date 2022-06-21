# coding: utf-8
"""

"""
import typing
from copy import deepcopy
import datetime
import io
import itertools
import json
import math
import os
import zipfile

import flask
import flask_babel
import flask_login
import itsdangerous
import requests
import werkzeug.utils
from flask_babel import _

from ..logic.federation import update_poke_component
from . import frontend
from .. import logic
from .. import models
from .. import db
from ..logic import user_log, object_log, comments, object_sorting
from ..logic.actions import get_action, get_actions, get_action_type, get_action_types
from ..logic.action_type_translations import get_action_types_with_translations_in_language, get_action_type_with_translation_in_language
from ..logic.action_translations import get_action_with_translation_in_language
from ..logic.action_permissions import get_user_action_permissions, get_sorted_actions_for_user
from ..logic.object_permissions import Permissions, get_user_object_permissions, get_object_permissions_for_all_users, get_object_permissions_for_users, get_objects_with_permissions, get_object_info_with_permissions, get_object_permissions_for_groups, get_object_permissions_for_projects, request_object_permissions
from ..logic.instrument_translations import get_instrument_with_translation_in_language
from ..logic.shares import get_shares_for_object, add_object_share, update_object_share
from ..logic.users import get_user, get_users, get_users_by_name, get_users_for_component
from ..logic.schemas import validate, generate_placeholder
from ..logic.settings import get_user_settings, set_user_settings
from ..logic.object_search import generate_filter_func, wrap_filter_func
from ..logic.groups import get_group, get_user_groups
from ..logic.objects import create_object, create_object_batch, update_object, get_object, get_object_versions, get_fed_object
from ..logic.object_log import ObjectLogEntryType
from ..logic.projects import get_project, get_user_projects, get_user_project_permissions
from ..logic.locations import get_location, get_object_ids_at_location, get_object_location_assignment, get_object_location_assignments, assign_location_to_object, get_locations_tree
from ..logic.location_permissions import get_user_location_permissions, get_locations_with_user_permissions
from ..logic.languages import get_language_by_lang_code, get_language, get_languages, Language, get_user_language
from ..logic.files import FileLogEntryType
from ..logic.errors import ObjectDoesNotExistError, UserDoesNotExistError, ActionDoesNotExistError, ValidationError, LocationDoesNotExistError, ActionTypeDoesNotExistError, ComponentDoesNotExistError
from ..logic.components import get_component, get_component_by_uuid, get_components
from ..logic.notebook_templates import get_notebook_templates
from .objects_forms import ObjectForm, ObjectVersionRestoreForm, CommentForm, FileForm, FileInformationForm, FileHidingForm, ObjectLocationAssignmentForm, ExternalLinkForm, ObjectPublicationForm, CopyPermissionsForm, ObjectNewShareAccessForm, ObjectEditShareAccessForm
from .permission_forms import PermissionsForm, UserPermissionsForm, GroupPermissionsForm, ProjectPermissionsForm, handle_permission_forms, set_up_permissions_forms
from ..utils import object_permissions_required
from .utils import generate_qrcode, get_user_if_exists, default_format_datetime, custom_format_number
from .object_form_parser import parse_form_data
from .labels import create_labels, PAGE_SIZES, DEFAULT_PAPER_FORMAT, HORIZONTAL_LABEL_MARGIN, VERTICAL_LABEL_MARGIN, mm
from . import pdfexport
from .utils import check_current_user_is_not_readonly, get_location_name, get_search_paths
from ..logic.utils import get_translated_text

__author__ = 'Florian Rhiem <f.rhiem@fz-juelich.de>'


def on_unauthorized(object_id):
    permissions_by_user = get_object_permissions_for_users(object_id)
    has_grant_user = any(
        Permissions.GRANT in permissions
        for permissions in permissions_by_user.values()
    )
    return flask.render_template('objects/unauthorized.html', object_id=object_id, has_grant_user=has_grant_user), 403


@frontend.route('/objects/')
@flask_login.login_required
def objects():
    object_ids = flask.request.args.get('ids', '')
    objects = []
    display_properties = []
    display_property_titles = {}
    user_language_id = logic.languages.get_user_language(flask_login.current_user).id
    if 'display_properties' in flask.request.args:
        for property_info in itertools.chain(*[
            display_properties_str.split(',')
            for display_properties_str in flask.request.args.getlist('display_properties')
        ]):
            if ':' in property_info:
                property_name, property_title = property_info.split(':', 1)
            else:
                property_name, property_title = property_info, None
            if property_name not in display_properties:
                display_properties.append(property_name)
            if property_title is not None:
                display_property_titles[property_name] = flask.escape(property_title)

    all_actions = get_sorted_actions_for_user(
        user_id=flask_login.current_user.id
    )
    all_action_types = logic.action_type_translations.get_action_types_with_translations_in_language(
        language_id=user_language_id,
        filter_fed_defaults=True
    )
    search_paths, search_paths_by_action, search_paths_by_action_type = get_search_paths(
        actions=all_actions,
        action_types=all_action_types,
        path_depth_limit=1,
        valid_property_types=(
            'text',
            'bool',
            'quantity',
            'datetime',
            'user',
            'object_reference',
            'sample',
            'measurement',
            'plotly_chart',
        )
    )

    name_only = True
    implicit_action_type = None
    if object_ids:
        object_ids = object_ids.split(',')
        try:
            object_ids = [int(object_id) for object_id in object_ids]
        except ValueError:
            object_ids = []

        readable_object_ids = []
        for object_id in object_ids:
            if Permissions.READ in get_user_object_permissions(object_id, user_id=flask_login.current_user.id):
                readable_object_ids.append(object_id)
        object_ids = readable_object_ids
        for object_id in object_ids:
            try:
                objects.append(get_object(object_id))
            except logic.errors.ObjectDoesNotExistError:
                pass
        query_string = ''
        use_advanced_search = False
        must_use_advanced_search = False
        advanced_search_had_error = False
        search_notes = []
        search_tree = None
        pagination_limit = None
        pagination_offset = None
        pagination_enabled = True
        num_objects_found = len(objects)
        sorting_property_name = None
        sorting_order_name = None
        show_filters = False
        all_actions = []
        filter_action_ids = []
        all_action_types = []
        filter_action_type_ids = []
        all_locations = []
        filter_location_ids = []
        filter_related_user_id = None
        filter_doi = None
        filter_user_id = None
        filter_user_permissions = None
        filter_group_id = None
        filter_group_permissions = None
        filter_project_id = None
        filter_project_permissions = None
        all_publications = []
    else:
        pagination_enabled = True

        show_filters = True
        all_locations = get_locations_with_user_permissions(flask_login.current_user.id, Permissions.READ)
        if 'location_ids' in flask.request.args:
            try:
                filter_location_ids = [
                    int(id_str.strip())
                    for id_str in itertools.chain(*[
                        location_ids_str.split(',')
                        for location_ids_str in flask.request.args.getlist('location_ids')
                    ])
                ]
            except ValueError:
                flask.flash(_('Unable to parse location IDs.'), 'error')
                return flask.abort(400)
            all_location_ids = [
                location.id
                for location in all_locations
            ]
            if any(location_id not in all_location_ids for location_id in filter_location_ids):
                flask.flash(_('Invalid location ID.'), 'error')
                return flask.abort(400)
            if 'location' in flask.request.args:
                flask.flash(_('Only one of location_ids and location may be set.'), 'error')
                return flask.abort(400)
        else:
            filter_location_ids = None
        if 'location' in flask.request.args:
            try:
                location_id = int(flask.request.args.get('location', ''))
                if Permissions.READ in get_user_location_permissions(location_id, flask_login.current_user.id):
                    filter_location_ids = [location_id]
                else:
                    flask.flash(_('You do not have the required permissions to access this location.'), 'error')
            except ValueError:
                flask.flash(_('Unable to parse location IDs.'), 'error')
            except LocationDoesNotExistError:
                flask.flash(_('No location with the given ID exists.'), 'error')

        if 'action_ids' in flask.request.args:
            try:
                filter_action_ids = [
                    int(id_str.strip())
                    for id_str in itertools.chain(*[
                        action_ids_str.split(',')
                        for action_ids_str in flask.request.args.getlist('action_ids')
                    ])
                ]
            except ValueError:
                flask.flash(_('Unable to parse action IDs.'), 'error')
                return flask.abort(400)
            all_action_ids = [
                action.id
                for action in all_actions
            ]
            if any(action_id not in all_action_ids for action_id in filter_action_ids):
                flask.flash(_('Invalid action ID.'), 'error')
                return flask.abort(400)
            if 'action' in flask.request.args:
                flask.flash(_('Only one of action_ids and action may be set.'), 'error')
                return flask.abort(400)
        else:
            filter_action_ids = None

        if 'action_type_ids' in flask.request.args:
            try:
                filter_action_type_ids = [
                    int(id_str.strip())
                    for id_str in itertools.chain(*[
                        action_type_ids_str.split(',')
                        for action_type_ids_str in flask.request.args.getlist('action_type_ids')
                    ])
                ]
            except ValueError:
                flask.flash(_('Unable to parse action type IDs.'), 'error')
                return flask.abort(400)
            all_action_type_ids = [
                action_type.id
                for action_type in all_action_types
            ]
            if any(action_type_id not in all_action_type_ids for action_type_id in filter_action_type_ids):
                flask.flash(_('Invalid action type ID.'), 'error')
                return flask.abort(400)
            if 't' in flask.request.args:
                flask.flash(_('Only one of action_type_ids and t may be set.'), 'error')
                return flask.abort(400)
        else:
            filter_action_type_ids = None
        try:
            action_id = int(flask.request.args.get('action', ''))
        except ValueError:
            action_id = None
        if action_id is None and filter_action_ids is not None and len(filter_action_ids) == 1:
            action_id = filter_action_ids[0]
        if action_id is not None:
            action = get_action_with_translation_in_language(action_id, user_language_id, use_fallback=True)
            implicit_action_type = get_action_type_with_translation_in_language(action.type_id, user_language_id)
            action_schema = action.schema
            action_display_properties = action_schema.get('displayProperties', [])
            for property_name in action_display_properties:
                if property_name not in display_properties:
                    display_properties.append(property_name)
                if property_name not in display_property_titles:
                    display_property_titles[property_name] = flask.escape(get_translated_text(action_schema['properties'][property_name]['title']))
        else:
            action_type_id = flask.request.args.get('t', '')
            if action_type_id is not None:
                try:
                    action_type_id = int(action_type_id)
                except ValueError:
                    # ensure old links still function
                    action_type_id = {
                        'samples': models.ActionType.SAMPLE_CREATION,
                        'measurements': models.ActionType.MEASUREMENT,
                        'simulations': models.ActionType.SIMULATION
                    }.get(action_type_id, None)
            if action_type_id is not None:
                try:
                    action_type = get_action_type_with_translation_in_language(
                        action_type_id=action_type_id,
                        language_id=user_language_id
                    )
                except ActionTypeDoesNotExistError:
                    action_type = None
            else:
                action_type = None
            if filter_action_type_ids is None and action_type is not None:
                filter_action_type_ids = [action_type.id]

        if filter_action_ids is None and action_id is not None:
            filter_action_ids = [action_id]
        if display_properties:
            name_only = False

        try:
            filter_related_user_id = int(flask.request.args.get('related_user', ''))
            get_user(filter_related_user_id)
        except ValueError:
            filter_related_user_id = None
        except UserDoesNotExistError:
            filter_related_user_id = None

        all_publications = logic.publications.get_publications_for_user(flask_login.current_user.id)
        try:
            filter_doi = logic.publications.simplify_doi(flask.request.args.get('doi', ''))
        except logic.errors.InvalidDOIError:
            filter_doi = None

        filter_user_permissions = None
        try:
            filter_user_id = int(flask.request.args.get('user', ''))
            get_user(filter_user_id)
        except ValueError:
            filter_user_id = None
        except UserDoesNotExistError:
            filter_user_id = None
        else:
            filter_user_permissions = {
                'read': Permissions.READ,
                'write': Permissions.WRITE,
                'grant': Permissions.GRANT
            }.get(flask.request.args.get('user_permissions', '').lower(), Permissions.READ)

        filter_group_permissions = None
        try:
            filter_group_id = int(flask.request.args.get('group', ''))
            group_member_ids = logic.groups.get_group_member_ids(filter_group_id)
        except ValueError:
            filter_group_id = None
        except logic.errors.GroupDoesNotExistError:
            filter_group_id = None
        else:
            if flask_login.current_user.id not in group_member_ids:
                return flask.abort(403)
            filter_group_permissions = {
                'read': Permissions.READ,
                'write': Permissions.WRITE,
                'grant': Permissions.GRANT
            }.get(flask.request.args.get('group_permissions', '').lower(), Permissions.READ)

        filter_project_permissions = None
        try:
            filter_project_id = int(flask.request.args.get('project', ''))
            get_project(filter_project_id)
        except ValueError:
            filter_project_id = None
        except logic.errors.ProjectDoesNotExistError:
            filter_project_id = None
        else:
            if Permissions.READ not in get_user_project_permissions(project_id=filter_project_id, user_id=flask_login.current_user.id, include_groups=True):
                return flask.abort(403)
            filter_project_permissions = {
                'read': Permissions.READ,
                'write': Permissions.WRITE,
                'grant': Permissions.GRANT
            }.get(flask.request.args.get('project_permissions', '').lower(), Permissions.READ)

        if flask.request.args.get('limit', '') == 'all':
            pagination_limit = None
        else:
            try:
                pagination_limit = int(flask.request.args.get('limit', ''))
            except ValueError:
                pagination_limit = None
            else:
                if pagination_limit <= 0:
                    pagination_limit = None
                elif pagination_limit >= 1000:
                    pagination_limit = 1000

            # default objects per page
            if pagination_limit is None:
                pagination_limit = get_user_settings(flask_login.current_user.id)['OBJECTS_PER_PAGE']
            else:
                set_user_settings(flask_login.current_user.id, {'OBJECTS_PER_PAGE': pagination_limit})

        try:
            pagination_offset = int(flask.request.args.get('offset', ''))
        except ValueError:
            pagination_offset = None
        else:
            if pagination_offset < 0:
                pagination_offset = None
            elif pagination_offset > 100000000:
                pagination_offset = 100000000
        if pagination_limit is not None and pagination_offset is None:
            pagination_offset = 0

        sorting_order_name = flask.request.args.get('order', None)
        if sorting_order_name == 'asc':
            sorting_order = object_sorting.ascending
        elif sorting_order_name == 'desc':
            sorting_order = object_sorting.descending
        else:
            sorting_order = None

        sorting_property_name = flask.request.args.get('sortby', None)

        if sorting_order is None:
            if sorting_property_name is None:
                sorting_order_name = 'desc'
                sorting_order = object_sorting.descending
            else:
                sorting_order_name = 'asc'
                sorting_order = object_sorting.ascending
        if sorting_property_name is None:
            sorting_property_name = '_object_id'
        else:
            name_only = False
        if sorting_property_name == '_object_id':
            sorting_property = object_sorting.object_id()
        elif sorting_property_name == '_creation_date':
            sorting_property = object_sorting.creation_date()
        elif sorting_property_name == '_last_modification_date':
            sorting_property = object_sorting.last_modification_date()
        else:
            sorting_property = object_sorting.property_value(sorting_property_name)

        sorting_function = sorting_order(sorting_property)

        query_string = flask.request.args.get('q', '')
        if query_string:
            name_only = False
        search_tree = None
        use_advanced_search = flask.request.args.get('advanced', None) is not None
        must_use_advanced_search = use_advanced_search
        advanced_search_had_error = False
        additional_search_notes = []
        if not use_advanced_search and query_string:
            if filter_user_id is None:
                users = get_users_by_name(query_string)
                if len(users) == 1:
                    user = users[0]
                    filter_user_id = user.id
                    query_string = ''
                elif len(users) > 1:
                    additional_search_notes.append(('error', "There are multiple users with this name.", 0, 0))
            if filter_doi is None and query_string.startswith('doi:'):
                try:
                    filter_doi = logic.publications.simplify_doi(query_string)
                    query_string = ''
                except logic.errors.InvalidDOIError:
                    pass
        try:
            filter_func, search_tree, use_advanced_search = generate_filter_func(query_string, use_advanced_search)
        except Exception:
            # TODO: ensure that advanced search does not cause exceptions
            if use_advanced_search:
                advanced_search_had_error = True

                def filter_func(data, search_notes):
                    """ Return all objects"""
                    search_notes.append(('error', "Unable to parse search expression", 0, len(query_string)))
                    return False
            else:
                raise
        filter_func, search_notes = wrap_filter_func(filter_func)
        search_notes.extend(additional_search_notes)

        if filter_location_ids is not None:
            object_ids_at_location = set()
            for location_id in filter_location_ids:
                object_ids_at_location.update(get_object_ids_at_location(location_id))
        else:
            object_ids_at_location = None
        if filter_related_user_id is None:
            object_ids_for_user = None
        else:
            object_ids_for_user = set(user_log.get_user_related_object_ids(filter_related_user_id))
        if filter_doi is None:
            object_ids_for_doi = None
        else:
            object_ids_for_doi = set(logic.publications.get_object_ids_linked_to_doi(filter_doi))

        if use_advanced_search and not must_use_advanced_search:
            search_notes.append(('info', _("The advanced search was used automatically. Search for \"%(query_string)s\" to use the simple search.", query_string=query_string), 0, 0))
        try:
            object_ids: typing.Optional[typing.Set[int]] = None
            if object_ids_at_location is not None:
                if object_ids is None:
                    object_ids = object_ids_at_location
                else:
                    object_ids = object_ids.intersection(object_ids_at_location)
            if object_ids_for_user is not None:
                if object_ids is None:
                    object_ids = object_ids_for_user
                else:
                    object_ids = object_ids.intersection(object_ids_for_user)
            if object_ids_for_doi is not None:
                if object_ids is None:
                    object_ids = object_ids_for_doi
                else:
                    object_ids = object_ids.intersection(object_ids_for_doi)

            if object_ids is not None:
                pagination_enabled = False
                pagination_limit = None
                pagination_offset = None
            if object_ids is not None and not object_ids:
                objects = []
                num_objects_found = 0
            else:
                num_objects_found_list = []
                objects = get_objects_with_permissions(
                    user_id=flask_login.current_user.id,
                    permissions=Permissions.READ,
                    filter_func=filter_func,
                    sorting_func=sorting_function,
                    limit=pagination_limit,
                    offset=pagination_offset,
                    action_ids=filter_action_ids,
                    action_type_ids=filter_action_type_ids,
                    other_user_id=filter_user_id,
                    other_user_permissions=filter_user_permissions,
                    project_id=filter_project_id,
                    project_permissions=filter_project_permissions,
                    group_id=filter_group_id,
                    group_permissions=filter_group_permissions,
                    object_ids=object_ids,
                    num_objects_found=num_objects_found_list,
                    name_only=name_only
                )
                num_objects_found = num_objects_found_list[0]
        except Exception as e:
            search_notes.append(('error', "Error during search: {}".format(e), 0, 0))
            objects = []
            num_objects_found = 0
        if any(note[0] == 'error' for note in search_notes):
            objects = []
            advanced_search_had_error = True

    cached_actions = {None: None}
    cached_users = {None: None}

    for i, obj in enumerate(objects):
        if obj.version_id == 0:
            original_object = obj
        else:
            original_object = get_object(object_id=obj.object_id, version_id=0)
        if obj.action_id is not None and obj.action_id not in cached_actions:
            cached_actions[obj.action_id] = get_action(obj.action_id)
        if obj.user_id is not None and obj.user_id not in cached_users:
            cached_users[obj.user_id] = get_user(obj.user_id)
        if obj.user_id is not None and original_object.user_id not in cached_users:
            cached_users[original_object.user_id] = get_user(original_object.user_id)
        objects[i] = {
            'object_id': obj.object_id,
            'created_by': cached_users[original_object.user_id],
            'created_at': original_object.utc_datetime,
            'modified_by': cached_users[obj.user_id],
            'last_modified_at': obj.utc_datetime,
            'data': obj.data,
            'schema': obj.schema,
            'name': obj.name,
            'action': cached_actions[obj.action_id],
            'fed_object_id': obj.fed_object_id,
            'component_id': obj.component_id,
            'display_properties': {},
            'component': obj.component
        }

        for property_name in display_properties:
            objects[i]['display_properties'][property_name] = None
            if not objects[i]['data'] or not objects[i]['schema']:
                # object does not have any properties
                continue
            if property_name not in objects[i]['schema']['properties']:
                # object must not have this property
                continue
            property_schema = objects[i]['schema']['properties'][property_name]
            if property_name not in objects[i]['data']:
                # object does not have this property
                continue
            property_data = objects[i]['data'][property_name]
            if not isinstance(property_data, dict) or '_type' not in property_data:
                # property data cannot be displayed (e.g. None/null or array)
                continue
            objects[i]['display_properties'][property_name] = (property_data, property_schema)

    def build_modified_url(**query_parameters):
        for key in flask.request.args:
            if key not in query_parameters:
                query_parameters[key] = flask.request.args.getlist(key)
        return flask.url_for(
            '.objects',
            **query_parameters
        )

    action_ids = {
        object['action'].id for object in objects if object['action'] is not None
    }
    action_translations = {}
    for id in action_ids:
        action_translations[id] = logic.action_translations.get_action_translation_for_action_in_language(
            action_id=id,
            language_id=user_language_id,
            use_fallback=True
        )

    default_property_titles = {
        'tags': _('Tags'),
        'hazards': _('Hazards')
    }
    for property_name in display_properties:
        if display_property_titles.get(property_name) is None:
            property_titles = set()
            for id in action_ids:
                property_info = search_paths_by_action.get(id, {}).get(property_name)
                if property_info is not None and 'titles' in property_info:
                    property_titles.update(property_info['titles'])
            if property_titles:
                property_title = ', '.join(sorted(list(property_titles)))
            elif property_name in default_property_titles:
                property_title = flask.escape(default_property_titles[property_name])
            else:
                property_title = flask.escape(property_name)
            display_property_titles[property_name] = property_title

    last_edit_info = None
    creation_info = None
    action_info = None
    if 'object_list_options' in flask.request.args:
        creation_info = set()
        for creation_info_str in flask.request.args.getlist('creation_info'):
            creation_info_str = creation_info_str.strip().lower()
            if creation_info_str in {'user', 'date'}:
                creation_info.add(creation_info_str)
        creation_info = list(creation_info)

        last_edit_info = set()
        for last_edit_info_str in flask.request.args.getlist('last_edit_info'):
            last_edit_info_str = last_edit_info_str.strip().lower()
            if last_edit_info_str in {'user', 'date'}:
                last_edit_info.add(last_edit_info_str)
        last_edit_info = list(last_edit_info)

        action_info = set()
        for action_info_str in flask.request.args.getlist('action_info'):
            action_info_str = action_info_str.strip().lower()
            if action_info_str in {'instrument', 'action'}:
                action_info.add(action_info_str)
        action_info = list(action_info)

    if creation_info is None:
        creation_info = ['user', 'date']
    if last_edit_info is None:
        last_edit_info = ['user', 'date']
    if action_info is None:
        if filter_action_ids is None or len(filter_action_ids) != 1:
            action_info = ['instrument', 'action']
        else:
            action_info = []

    object_name_plural = flask_babel.gettext('Objects')

    filter_action_type_infos = []
    if filter_action_type_ids:
        for action_type_id in filter_action_type_ids:
            action_type = get_action_type_with_translation_in_language(action_type_id, user_language_id)
            action_type_name = action_type.translation.name
            action_type_component = get_component(action_type.component_id) if action_type.component_id is not None else None
            if not action_type_name:
                action_type_name = flask_babel.gettext('Unnamed Action Type')
            filter_action_type_infos.append({
                'id': action_type_id,
                'name': action_type_name,
                'url': flask.url_for('.actions', t=action_type_id),
                'fed_id': action_type.fed_id,
                'component_name': action_type_component.get_name() if action_type_component is not None else None
            })

            if filter_action_type_ids and len(filter_action_type_ids) == 1:
                object_name_plural = action_type.translation.object_name_plural
    elif implicit_action_type is not None:
        object_name_plural = implicit_action_type.translation.object_name_plural

    filter_action_infos = []
    if filter_action_ids:
        for action_id in filter_action_ids:
            action = get_action_with_translation_in_language(action_id, user_language_id, use_fallback=True)
            action_name = action.translation.name
            if not action_name:
                action_name = flask_babel.gettext('Unnamed Action')
            action_name += f' (#{action_id})'
            filter_action_infos.append({
                'name': action_name,
                'url': flask.url_for('.action', action_id=action_id),
                'fed_id': action.fed_id,
                'component_name': action.component.get_name() if action.component is not None else None
            })

    filter_location_infos = []
    if filter_location_ids:
        for location_id in filter_location_ids:
            location = get_location(location_id)
            location_name = get_location_name(location_id, include_id=True)
            filter_location_infos.append({
                'name': location_name,
                'url': flask.url_for('.location', location_id=location_id),
                'fed_id': location.fed_id,
                'component_name': location.component.get_name() if location.component is not None else None
            })

    if filter_related_user_id is not None:
        user = get_user(filter_related_user_id)
        filter_related_user_info = {
            'name': user.get_name(),
            'url': flask.url_for('.user_profile', user_id=filter_related_user_id),
            'fed_id': user.fed_id,
            'component_name': user.component.get_name() if user.component is not None else None,
        }
    else:
        filter_related_user_info = None

    if filter_user_permissions is not None and filter_user_id is not None:
        filter_user_permissions_info = {
            'name': None,
            'url': None,
            'fed_id': None,
            'component_name': None,
            'permissions': flask_babel.gettext(filter_user_permissions.name.title())
        }
        if filter_user_id != flask_login.current_user.id:
            user = get_user(filter_user_id)
            filter_user_permissions_info.update({
                'name': user.get_name(),
                'url': flask.url_for('.user_profile', user_id=filter_user_id),
                'fed_id': user.fed_id,
                'component_name': user.component.get_name() if user.component is not None else None,
            })
        elif filter_user_permissions == Permissions.READ:
            # READ permissions for the current user as always necessary, so this filter does not need to be displayed
            filter_user_permissions_info = None
    else:
        filter_user_permissions_info = None

    if filter_group_permissions is not None and filter_group_id is not None:
        group = get_group(filter_group_id)
        filter_group_permissions_info = {
            'name': f'{get_translated_text(group.name)} (#{filter_group_id})',
            'url': flask.url_for('.group', group_id=filter_group_id),
            'permissions': flask_babel.gettext(filter_group_permissions.name.title())
        }
    else:
        filter_group_permissions_info = None

    if filter_project_permissions is not None and filter_project_id is not None:
        project = get_project(filter_project_id)
        filter_project_permissions_info = {
            'name': f'{get_translated_text(project.name)} (#{filter_project_id})',
            'url': flask.url_for('.project', project_id=filter_project_id),
            'permissions': flask_babel.gettext(filter_project_permissions.name.title())
        }
    else:
        filter_project_permissions_info = None

    if filter_doi:
        filter_doi_info = {
            'doi': filter_doi,
            'title': dict(all_publications).get(filter_doi)
        }
    else:
        filter_doi_info = None

    return flask.render_template(
        'objects/objects.html',
        objects=objects,
        display_properties=display_properties,
        display_property_titles=display_property_titles,
        search_query=query_string,
        use_advanced_search=use_advanced_search,
        must_use_advanced_search=must_use_advanced_search,
        advanced_search_had_error=advanced_search_had_error,
        search_notes=search_notes,
        search_tree=search_tree,
        search_paths=search_paths,
        search_paths_by_action=search_paths_by_action,
        search_paths_by_action_type=search_paths_by_action_type,
        Permissions=Permissions,
        creation_info=creation_info,
        last_edit_info=last_edit_info,
        action_info=action_info,
        action_translations=action_translations,
        object_name_plural=object_name_plural,
        filter_action_type_infos=filter_action_type_infos,
        filter_action_infos=filter_action_infos,
        filter_location_infos=filter_location_infos,
        filter_related_user_info=filter_related_user_info,
        filter_user_permissions_info=filter_user_permissions_info,
        filter_group_permissions_info=filter_group_permissions_info,
        filter_project_permissions_info=filter_project_permissions_info,
        filter_doi_info=filter_doi_info,
        show_filters=show_filters,
        all_actions=all_actions,
        filter_action_ids=filter_action_ids,
        all_action_types=all_action_types,
        filter_action_type_ids=filter_action_type_ids,
        all_locations=all_locations,
        filter_location_ids=filter_location_ids,
        all_publications=all_publications,
        filter_doi=filter_doi,
        get_object_if_current_user_has_read_permissions=get_object_if_current_user_has_read_permissions,
        get_instrument_translation_for_instrument_in_language=logic.instrument_translations.get_instrument_translation_for_instrument_in_language,
        build_modified_url=build_modified_url,
        sorting_property=sorting_property_name,
        sorting_order=sorting_order_name,
        limit=pagination_limit,
        offset=pagination_offset,
        pagination_enabled=pagination_enabled,
        num_objects_found=num_objects_found,
        get_user=get_user,
        get_component=get_component
    )


def get_sub_data_and_schema(data, schema, id_prefix):
    sub_data = data
    sub_schema = schema
    try:
        for key in id_prefix.split('__'):
            if sub_schema['type'] == 'array':
                key = int(key)
                sub_schema = sub_schema['items']
            elif sub_schema['type'] == 'object':
                sub_schema = sub_schema['properties'][key]
            else:
                raise ValueError('invalid type')
            if isinstance(key, int):
                while key >= len(sub_data):
                    sub_data.append(generate_placeholder(sub_schema))
            elif key not in sub_data:
                sub_data[key] = generate_placeholder(sub_schema)
            sub_data = sub_data[key]
        if sub_schema['type'] != 'array':
            raise ValueError('invalid type')
    except (ValueError, KeyError, IndexError, TypeError):
        # TODO: error handling/logging?
        raise ValueError('invalid action')
    return sub_data, sub_schema


def apply_action_to_form_data(action, form_data):
    new_form_data = form_data
    action_id_prefix, action_index, action_type = action[len('action_'):].rsplit('__', 2)
    if action_type == 'delete':
        deleted_item_index = int(action_index)
        parent_id_prefix = action_id_prefix
        new_form_data = {}
        for name in form_data:
            if not name.startswith(parent_id_prefix + '__') or '__' not in name[len(parent_id_prefix) + 2:]:
                new_form_data[name] = form_data[name]
            else:
                item_index, id_suffix = name[len(parent_id_prefix) + 2:].split('__', 1)
                item_index = int(item_index)
                if item_index < deleted_item_index:
                    new_form_data[name] = form_data[name]
                if item_index > deleted_item_index:
                    new_name = parent_id_prefix + '__' + str(item_index - 1) + '__' + id_suffix
                    new_form_data[new_name] = form_data[name]
    return new_form_data


def apply_action_to_data(action, data, schema):
    action_id_prefix, action_index, action_type = action[len('action_'):].rsplit('__', 2)
    if action_type not in ('add', 'delete', 'addcolumn', 'deletecolumn'):
        raise ValueError('invalid action')
    sub_data, sub_schema = get_sub_data_and_schema(data, schema, action_id_prefix.split('__', 1)[1])
    if action_type in ('addcolumn', 'deletecolumn') and (sub_schema["style"] != "table" or sub_schema["items"]["type"] != "array"):
        raise ValueError('invalid action')
    num_existing_items = len(sub_data)
    if action_type == 'add':
        if 'maxItems' not in sub_schema or num_existing_items < sub_schema["maxItems"]:
            sub_data.append(generate_placeholder(sub_schema["items"]))
            if isinstance(sub_data[-1], list) and sub_schema.get('style') == 'table':
                num_existing_columns = sub_schema["items"].get("minItems", 0)
                for row in sub_data:
                    num_existing_columns = max(num_existing_columns, len(row))
                while len(sub_data[-1]) < num_existing_columns:
                    sub_data[-1].append(None)
    elif action_type == 'delete':
        action_index = int(action_index)
        if ('minItems' not in sub_schema or num_existing_items > sub_schema["minItems"]) and action_index < num_existing_items:
            del sub_data[action_index]
    else:
        num_existing_columns = sub_schema["items"].get("minItems", 0)
        for row in sub_data:
            num_existing_columns = max(num_existing_columns, len(row))
        if action_type == 'addcolumn':
            if 'maxItems' not in sub_schema["items"] or num_existing_columns < sub_schema["items"]["maxItems"]:
                num_existing_columns += 1
                for row in sub_data:
                    while len(row) < num_existing_columns:
                        row.append(generate_placeholder(sub_schema["items"]["items"]))
        elif action_type == 'deletecolumn':
            if num_existing_columns > sub_schema.get("minItems", 0):
                num_existing_columns -= 1
                for row in sub_data:
                    while len(row) > num_existing_columns:
                        del row[-1]


def show_object_form(object, action, previous_object=None, should_upgrade_schema=False, placeholder_data=None):
    if object is None and previous_object is None:
        data = generate_placeholder(action.schema)
        if placeholder_data:
            for path, value in placeholder_data.items():
                try:
                    sub_data = data
                    for step in path[:-1]:
                        sub_data = sub_data[step]
                    sub_data[path[-1]] = value
                except Exception:
                    # Ignore invalid placeholder data
                    pass
    elif object is None and previous_object is not None:
        data = logic.schemas.copy_data(previous_object.data, previous_object.schema)
    else:
        data = object.data
    previous_object_schema = None
    mode = 'edit'
    if should_upgrade_schema:
        mode = 'upgrade'
        assert object is not None
        schema = action.schema
        data, upgrade_warnings = logic.schemas.convert_to_schema(object.data, object.schema, action.schema)
        for upgrade_warning in upgrade_warnings:
            flask.flash(upgrade_warning, 'warning')
    elif object is not None:
        schema = object.schema
    elif previous_object is not None:
        schema = previous_object.schema
        previous_object_schema = schema
    else:
        schema = action.schema

    if action is not None and action.instrument is not None and flask_login.current_user in action.instrument.responsible_users:
        may_create_log_entry = True
        create_log_entry_default = action.instrument.create_log_entry_default
        instrument_log_categories = logic.instrument_log_entries.get_instrument_log_categories(action.instrument.id)
        if 'create_instrument_log_entry' in flask.request.form:
            category_ids = []
            for category_id in flask.request.form.getlist('instrument_log_categories'):
                try:
                    if int(category_id) in [category.id for category in instrument_log_categories]:
                        category_ids.append(int(category_id))
                except Exception:
                    pass
        else:
            category_ids = None
    else:
        instrument_log_categories = None
        category_ids = None
        create_log_entry_default = None
        may_create_log_entry = False

    permissions_for_group_id = None
    permissions_for_project_id = None
    copy_permissions_object_id = None
    if object is None:
        if flask.request.form.get('permissions_method') == 'copy_permissions':
            if flask.request.form.get('copy_permissions_object_id'):
                copy_permissions_object_id = flask.request.form.get('copy_permissions_object_id')
                try:
                    copy_permissions_object_id = int(copy_permissions_object_id)
                    if Permissions.READ not in get_user_object_permissions(copy_permissions_object_id, flask_login.current_user.id):
                        flask.flash(_("Unable to copy permissions. Default permissions will be applied."), 'error')
                        copy_permissions_object_id = None
                except Exception:
                    flask.flash(_("Unable to copy permissions. Default permissions will be applied."), 'error')
                    copy_permissions_object_id = None
            else:
                flask.flash(_("No object selected. Default permissions will be applied."), 'error')
                copy_permissions_object_id = None
        elif flask.request.form.get('permissions_method') == 'permissions_for_group':
            if flask.request.form.get('permissions_for_group_group_id'):
                permissions_for_group_id = flask.request.form.get('permissions_for_group_group_id')
                try:
                    permissions_for_group_id = int(permissions_for_group_id)
                    if flask_login.current_user.id not in logic.groups.get_group_member_ids(permissions_for_group_id):
                        flask.flash(_("Unable to grant permissions to basic group. Default permissions will be applied."), 'error')
                        permissions_for_group_id = None
                except Exception:
                    flask.flash(_("Unable to grant permissions to basic group. Default permissions will be applied."), 'error')
                    permissions_for_group_id = None
            else:
                flask.flash(_("No basic group selected. Default permissions will be applied."), 'error')
                permissions_for_group_id = None
        elif flask.request.form.get('permissions_method') == 'permissions_for_project':
            if flask.request.form.get('permissions_for_project_project_id'):
                permissions_for_project_id = flask.request.form.get('permissions_for_project_project_id')
                try:
                    permissions_for_project_id = int(permissions_for_project_id)
                    if flask_login.current_user.id not in logic.projects.get_project_member_user_ids_and_permissions(permissions_for_project_id, include_groups=True):
                        flask.flash(_("Unable to grant permissions to project group. Default permissions will be applied."), 'error')
                        permissions_for_project_id = None
                except Exception:
                    flask.flash(_("Unable to grant permissions to project group. Default permissions will be applied."), 'error')
                    permissions_for_project_id = None
            else:
                flask.flash(_("No project group selected. Default permissions will be applied."), 'error')
                permissions_for_project_id = None

    if previous_object is not None:
        action_id = previous_object.action_id
        previous_object_id = previous_object.id
        has_grant_for_previous_object = Permissions.GRANT in get_user_object_permissions(user_id=flask_login.current_user.id, object_id=previous_object_id)
    else:
        action_id = action.id
        previous_object_id = None
        has_grant_for_previous_object = False
    errors = {}
    form_data = {}
    previous_actions = []
    serializer = itsdangerous.URLSafeSerializer(flask.current_app.config['SECRET_KEY'])
    form = ObjectForm()
    if flask.request.method != 'GET' and form.validate_on_submit():
        raw_form_data = {key: flask.request.form.getlist(key) for key in flask.request.form}
        form_data = {k: v[0] for k, v in raw_form_data.items()}
        if 'input_num_batch_objects' in form_data:
            try:
                # The form allows notations like '1.2e1' for '12', however
                # Python can only parse these as floats
                num_objects_in_batch = float(form_data['input_num_batch_objects'])
                if num_objects_in_batch == int(num_objects_in_batch):
                    num_objects_in_batch = int(num_objects_in_batch)
                else:
                    raise ValueError()
                if num_objects_in_batch > flask.current_app.config['MAX_BATCH_SIZE'] or num_objects_in_batch <= 0:
                    if num_objects_in_batch <= 0:
                        raise ValueError()
                    form_data['input_num_batch_objects'] = str(num_objects_in_batch)
                    errors['input_num_batch_objects'] = _('The maximum number of objects in one batch is %(max_batch_size)s.', max_batch_size=flask.current_app.config['MAX_BATCH_SIZE'])
            except ValueError:
                errors['input_num_batch_objects'] = _('The number of objects in batch must be an positive integer.')
                num_objects_in_batch = None
            else:
                form_data['input_num_batch_objects'] = str(num_objects_in_batch)
        else:
            num_objects_in_batch = None

        if 'previous_actions' in flask.request.form:
            try:
                previous_actions = serializer.loads(flask.request.form['previous_actions'])
            except itsdangerous.BadData:
                flask.abort(400)

        if "action_submit" in form_data:
            # The object name might need the batch number to match the pattern
            if schema.get('batch', False) and num_objects_in_batch is not None:
                name_suffix_format = schema.get('batch_name_format', '{:d}')
                try:
                    name_suffix_format.format(1)
                except (ValueError, KeyError):
                    name_suffix_format = '{:d}'
                if name_suffix_format:
                    example_name_suffix = name_suffix_format.format(1)
                else:
                    example_name_suffix = ''
                if 'object__name__text' in form_data:
                    batch_base_name = form_data['object__name__text']
                    raw_form_data['object__name__text'] = [batch_base_name + example_name_suffix]
                else:
                    enabled_languages = form_data.get('object__name__text_languages', [])
                    if 'en' not in enabled_languages:
                        enabled_languages.append('en')
                    for language_code in enabled_languages:
                        batch_base_name = form_data.get('object__name__text_' + language_code, '')
                        raw_form_data['object__name__text_' + language_code] = [batch_base_name + example_name_suffix]
            else:
                batch_base_name = None
                name_suffix_format = None
            object_data, parsing_errors = parse_form_data(raw_form_data, schema)
            errors.update(parsing_errors)
            if form_data['action_submit'] == 'inline_edit' and errors:
                return flask.jsonify({
                    'errors': errors
                }), 400
            if object_data is not None and not errors:
                try:
                    validate(object_data, schema)
                except ValidationError:
                    # TODO: proper logging
                    print('object schema validation failed')
                    # TODO: handle error
                    flask.abort(400)
                for markdown in logic.markdown_to_html.get_markdown_from_object_data(object_data):
                    markdown_as_html = logic.markdown_to_html.markdown_to_safe_html(markdown)
                    logic.markdown_images.mark_referenced_markdown_images_as_permanent(markdown_as_html)
                if object is None:
                    if schema.get('batch', False) and num_objects_in_batch is not None:
                        if 'name' in object_data and 'text' in object_data['name'] and name_suffix_format is not None and batch_base_name is not None:
                            data_sequence = []
                            for i in range(1, num_objects_in_batch + 1):
                                if name_suffix_format:
                                    name_suffix = name_suffix_format.format(i)
                                else:
                                    name_suffix = ''
                                object_data['name']['text'] = batch_base_name + name_suffix
                                data_sequence.append(deepcopy(object_data))
                        else:
                            data_sequence = [object_data] * num_objects_in_batch
                        objects = create_object_batch(
                            action_id=action.id,
                            data_sequence=data_sequence,
                            user_id=flask_login.current_user.id,
                            copy_permissions_object_id=copy_permissions_object_id,
                            permissions_for_group_id=permissions_for_group_id,
                            permissions_for_project_id=permissions_for_project_id
                        )
                        object_ids = [object.id for object in objects]
                        if category_ids is not None:
                            log_entry = logic.instrument_log_entries.create_instrument_log_entry(
                                instrument_id=action.instrument.id,
                                user_id=flask_login.current_user.id,
                                content='',
                                category_ids=category_ids
                            )
                            for object_id in object_ids:
                                logic.instrument_log_entries.create_instrument_log_object_attachment(
                                    instrument_log_entry_id=log_entry.id,
                                    object_id=object_id
                                )
                        flask.flash(_('The objects were created successfully.'), 'success')
                        return flask.redirect(flask.url_for('.objects', ids=','.join([str(object_id) for object_id in object_ids])))
                    else:
                        object = create_object(
                            action_id=action.id,
                            data=object_data,
                            user_id=flask_login.current_user.id,
                            previous_object_id=previous_object_id,
                            schema=previous_object_schema,
                            copy_permissions_object_id=copy_permissions_object_id,
                            permissions_for_group_id=permissions_for_group_id,
                            permissions_for_project_id=permissions_for_project_id
                        )
                        if category_ids is not None:
                            log_entry = logic.instrument_log_entries.create_instrument_log_entry(
                                instrument_id=action.instrument.id,
                                user_id=flask_login.current_user.id,
                                content='',
                                category_ids=category_ids
                            )
                            logic.instrument_log_entries.create_instrument_log_object_attachment(
                                instrument_log_entry_id=log_entry.id,
                                object_id=object.id
                            )
                        flask.flash(_('The object was created successfully.'), 'success')
                else:
                    if object_data != object.data or schema != object.schema:
                        update_object(object_id=object.id, user_id=flask_login.current_user.id, data=object_data, schema=schema)
                    flask.flash(_('The object was updated successfully.'), 'success')
                return flask.redirect(flask.url_for('.object', object_id=object.id))
        elif any(name.startswith('action_object__') and (name.endswith('__delete') or name.endswith('__add') or name.endswith('__addcolumn') or name.endswith('__deletecolumn')) for name in form_data):
            action = [name for name in form_data if name.startswith('action_')][0]
            previous_actions.append(action)

    if previous_actions:
        try:
            for action in previous_actions:
                apply_action_to_data(action, data, schema)
            form_data = apply_action_to_form_data(previous_actions[-1], form_data)
        except ValueError:
            flask.abort(400)

    if not flask.current_app.config["LOAD_OBJECTS_IN_BACKGROUND"]:
        referencable_objects = get_objects_with_permissions(
            user_id=flask_login.current_user.id,
            permissions=Permissions.READ
        )
        if object is not None:
            referencable_objects = [
                referencable_object
                for referencable_object in referencable_objects
                if referencable_object.object_id != object.object_id
            ]

    else:
        referencable_objects = []
        existing_objects = []
    sorted_actions = get_sorted_actions_for_user(
        user_id=flask_login.current_user.id
    )

    action_type_id_by_action_id = {}
    for action_type in get_action_types():
        for action in get_actions(action_type.id):
            if action_type.component_id is not None and action_type.fed_id < 0:
                action_type_id_by_action_id[action.id] = action_type.fed_id
            else:
                action_type_id_by_action_id[action.id] = action_type.id

    tags = [{'name': tag.name, 'uses': tag.uses} for tag in logic.tags.get_tags()]
    users = get_users(exclude_hidden=True)
    users.sort(key=lambda user: user.id)

    english = get_language(Language.ENGLISH)

    def insert_recipe_types(subschema):
        if subschema['type'] == 'object':
            if 'recipes' in subschema:
                for i, recipe in enumerate(subschema['recipes']):
                    for property in recipe['property_values']:
                        if subschema['properties'][property]['type'] == 'datetime':
                            subschema['recipes'][i]['property_values'][property] = {
                                'value': default_format_datetime(recipe['property_values'][property]['utc_datetime']) if
                                recipe['property_values'][property] is not None else None,
                                'type': subschema['properties'][property]['type']
                            }
                        elif subschema['properties'][property]['type'] == 'quantity':
                            subschema['recipes'][i]['property_values'][property] = {
                                'value': custom_format_number(
                                    recipe['property_values'][property]['magnitude_in_base_units']) if
                                recipe['property_values'][property] is not None else None,
                                'type': subschema['properties'][property]['type']
                            }
                        elif subschema['properties'][property]['type'] == 'text' and 'choices' in \
                                subschema['properties'][property]:
                            subschema['recipes'][i]['property_values'][property] = {
                                'value': recipe['property_values'][property]['text'] if recipe['property_values'][property] is not None else None,
                                'type': 'choice'
                            }
                        elif subschema['properties'][property]['type'] == 'text' and 'markdown' in \
                                subschema['properties'][property] and subschema['properties'][property]['markdown']:
                            subschema['recipes'][i]['property_values'][property] = {
                                'value': recipe['property_values'][property]['text'] if recipe['property_values'][property] is not None else None,
                                'type': 'markdown'
                            }
                        elif subschema['properties'][property]['type'] == 'text' and 'multiline' in \
                                subschema['properties'][property] and subschema['properties'][property]['multiline']:
                            subschema['recipes'][i]['property_values'][property] = {
                                'value': recipe['property_values'][property]['text'] if recipe['property_values'][property] is not None else None,
                                'type': 'multiline'
                            }
                        elif subschema['properties'][property]['type'] == 'text':
                            subschema['recipes'][i]['property_values'][property] = {
                                'value': recipe['property_values'][property]['text'] if recipe['property_values'][property] is not None else None,
                                'type': 'text'
                            }
                        elif subschema['properties'][property]['type'] == 'bool':
                            subschema['recipes'][i]['property_values'][property] = {
                                'value': recipe['property_values'][property]['value'],
                                'type': subschema['properties'][property]['type']
                            }
                        subschema['recipes'][i]['property_values'][property]['title'] = get_translated_text(
                            subschema['properties'][property]['title'])
                    subschema['recipes'][i]['name'] = get_translated_text(subschema['recipes'][i]['name'])
            for property in subschema['properties']:
                insert_recipe_types(subschema['properties'][property])
        elif subschema['type'] == 'array':
            insert_recipe_types(subschema['items'])

    insert_recipe_types(schema)

    if object is None:
        if get_action_type(action_type_id_by_action_id[action_id]).disable_create_objects:
            flask.flash(_('Creating objects with this action has been disabled.'), 'error')
            return flask.redirect(flask.url_for('.action', action_id=action_id))
        if not flask.current_app.config["LOAD_OBJECTS_IN_BACKGROUND"]:
            existing_objects = get_objects_with_permissions(
                user_id=flask_login.current_user.id,
                permissions=Permissions.GRANT
            )

        user_groups = logic.groups.get_user_groups(flask_login.current_user.id)
        user_projects = logic.projects.get_user_projects(flask_login.current_user.id, include_groups=True)

        return flask.render_template(
            'objects/forms/form_create.html',
            action_id=action_id,
            schema=schema,
            data=data,
            errors=errors,
            form_data=form_data,
            previous_actions=serializer.dumps(previous_actions),
            form=form,
            can_copy_permissions=True,
            existing_objects=existing_objects,
            user_groups=user_groups,
            user_projects=user_projects,
            referencable_objects=referencable_objects,
            sorted_actions=sorted_actions,
            action_type_id_by_action_id=action_type_id_by_action_id,
            get_object_if_current_user_has_read_permissions=get_object_if_current_user_has_read_permissions,
            ActionType=models.ActionType,
            datetime=datetime,
            tags=tags,
            users=users,
            may_create_log_entry=may_create_log_entry,
            instrument_log_categories=instrument_log_categories,
            create_log_entry_default=create_log_entry_default,
            previous_object_id=previous_object_id,
            has_grant_for_previous_object=has_grant_for_previous_object,
            languages=get_languages(only_enabled_for_input=True),
            get_component=get_component,
            ENGLISH=english
        )
    else:
        return flask.render_template(
            'objects/forms/form_edit.html',
            schema=schema,
            data=data,
            object_id=object.object_id,
            errors=errors,
            form_data=form_data,
            previous_actions=serializer.dumps(previous_actions),
            form=form,
            referencable_objects=referencable_objects,
            sorted_actions=sorted_actions,
            get_object_if_current_user_has_read_permissions=get_object_if_current_user_has_read_permissions,
            action_type_id_by_action_id=action_type_id_by_action_id,
            ActionType=models.ActionType,
            datetime=datetime,
            tags=tags,
            users=users,
            mode=mode,
            languages=get_languages(),
            get_component=get_component,
            ENGLISH=english
        )


def build_object_location_assignment_confirmation_url(object_location_assignment_id: int) -> None:
    confirmation_url = flask.url_for(
        'frontend.accept_responsibility_for_object',
        t=logic.security_tokens.generate_token(
            object_location_assignment_id,
            salt='confirm_responsibility',
            secret_key=flask.current_app.config['SECRET_KEY']
        ),
        _external=True
    )
    return confirmation_url


def get_project_if_it_exists(project_id):
    try:
        return get_project(project_id)
    except logic.errors.ProjectDoesNotExistError:
        return None


def show_inline_edit(obj, action, related_objects_tree):
    # Set view attributes
    user_language_id = get_user_language(flask_login.current_user).id
    english = get_language(Language.ENGLISH)

    object_id = obj.id

    user_permissions = get_user_object_permissions(object_id=object_id, user_id=flask_login.current_user.id)
    user_may_grant = Permissions.GRANT in user_permissions
    user_may_use_as_template = Permissions.READ in get_user_action_permissions(obj.action_id, user_id=flask_login.current_user.id)

    new_schema_available = True if action.schema != obj.schema else False

    instrument = get_instrument_with_translation_in_language(action.instrument_id,
                                                             user_language_id) if action.instrument else None
    object_type = get_action_type_with_translation_in_language(
        action_type_id=action.type_id,
        language_id=user_language_id
    ).translation.object_name
    object_log_entries = object_log.get_object_log_entries(object_id=obj.id, user_id=flask_login.current_user.id)

    dataverse_enabled = bool(flask.current_app.config['DATAVERSE_URL'])
    if dataverse_enabled:
        dataverse_url = logic.dataverse_export.get_dataverse_url(obj.id)
        show_dataverse_export = not dataverse_url
    else:
        dataverse_url = None
        show_dataverse_export = False

    serializer = itsdangerous.URLSafeTimedSerializer(flask.current_app.config['SECRET_KEY'], salt='mobile-upload')
    token = serializer.dumps([flask_login.current_user.id, object_id])
    mobile_upload_url = flask.url_for('.mobile_file_upload', object_id=object_id, token=token, _external=True)
    mobile_upload_qrcode = generate_qrcode(mobile_upload_url, should_cache=False)
    object_url = flask.url_for('.object', object_id=object_id, _external=True)
    object_qrcode = generate_qrcode(object_url, should_cache=True)

    readable_location_ids = [
        location.id
        for location in get_locations_with_user_permissions(flask_login.current_user.id, Permissions.READ)
    ]
    location_form = ObjectLocationAssignmentForm()
    locations_map, locations_tree = get_locations_tree()
    locations = [('-1', '—')]
    unvisited_location_ids_prefixes_and_subtrees = [(location_id, '', locations_tree[location_id]) for location_id in
                                                    locations_tree]
    while unvisited_location_ids_prefixes_and_subtrees:
        location_id, prefix, subtree = unvisited_location_ids_prefixes_and_subtrees.pop(0)
        location = locations_map[location_id]
        # skip unreadable locations, but allow processing their child locations
        # in case any of them are readable
        if location_id in readable_location_ids:
            locations.append((str(location_id), prefix + get_location_name(location, include_id=True)))
        prefix = f'{prefix}{get_location_name(location)} / '
        for location_id in sorted(subtree, key=lambda location_id: get_location_name(locations_map[location_id]), reverse=True):
            unvisited_location_ids_prefixes_and_subtrees.insert(
                0, (location_id, prefix, subtree[location_id])
            )

    location_form.location.choices = locations
    possible_responsible_users = [('-1', '—')]
    user_is_fed = {}
    for user in logic.users.get_users(exclude_hidden=True):
        possible_responsible_users.append((str(user.id), '{} (#{})'.format(user.name, user.id)))
        user_is_fed[str(user.id)] = user.fed_id is not None
    location_form.responsible_user.choices = possible_responsible_users

    measurement_actions = logic.action_translations.get_actions_with_translation_in_language(user_language_id,
                                                                                             models.ActionType.MEASUREMENT,
                                                                                             use_fallback=True)
    favorite_action_ids = logic.favorites.get_user_favorite_action_ids(flask_login.current_user.id)
    favorite_measurement_actions = [
        action
        for action in measurement_actions
        if action.id in favorite_action_ids and not action.is_hidden
    ]
    # Sort by: instrument name (independent actions first), action name
    favorite_measurement_actions.sort(key=lambda action: (
        action.user.name.lower() if action.user else '',
        get_instrument_with_translation_in_language(action.instrument_id,
                                                    user_language_id).translation.name.lower() if action.instrument else '',
        action.translation.name.lower()
    ))

    publication_form = ObjectPublicationForm()

    object_publications = logic.publications.get_publications_for_object(object_id=obj.id)
    user_may_link_publication = True

    notebook_templates = get_notebook_templates(
        object_id=obj.id,
        data=obj.data,
        schema=obj.schema,
        user_id=flask_login.current_user.id
    )

    linked_project = logic.projects.get_project_linked_to_object(object_id)

    object_languages = logic.languages.get_languages_in_object_data(obj.data)
    languages = []
    for lang_code in object_languages:
        languages.append(get_language_by_lang_code(lang_code))

    all_languages = get_languages()
    metadata_language = flask.request.args.get('language', None)
    if not any(
            language.lang_code == metadata_language
            for language in languages
    ):
        metadata_language = None

    view_kwargs = {
        "template_mode": "inline_edit",
        "show_object_type_and_id_on_object_page_text": get_user_settings(flask_login.current_user.id)["SHOW_OBJECT_TYPE_AND_ID_ON_OBJECT_PAGE"],
        "show_object_title": get_user_settings(flask_login.current_user.id)["SHOW_OBJECT_TITLE"],
        "measurement_type_name": logic.action_type_translations.get_action_type_translation_for_action_type_in_language(
            action_type_id=logic.actions.models.ActionType.MEASUREMENT,
            language_id=logic.languages.get_user_language(flask_login.current_user).id,
            use_fallback=True
        ).name,
        "metadata_language": metadata_language,
        "languages": languages,
        "all_languages": all_languages,
        "SUPPORTED_LOCALES": logic.locale.SUPPORTED_LOCALES,
        "ENGLISH": english,
        "object_type": object_type,
        "action": action,
        "action_type": get_action_type_with_translation_in_language(action.type_id, user_language_id),
        "instrument": instrument,
        "schema": obj.schema,
        "data": obj.data,
        "name": obj.name,
        "object_log_entries": object_log_entries,
        "ObjectLogEntryType": ObjectLogEntryType,
        "last_edit_datetime": obj.utc_datetime,
        "last_edit_user": get_user(obj.user_id),
        "object_id": object_id,
        "user_may_edit": True,
        "user_may_comment": True,
        "comments": comments.get_comments_for_object(object_id),
        "comment_form": CommentForm(),
        "files": logic.files.get_files_for_object(object_id),
        "file_source_instrument_exists": False,
        "file_source_jupyterhub_exists": False,
        "file_form": FileForm(),
        "external_link_form": ExternalLinkForm(),
        "external_link_error": flask.request.args.get('invalid_link_error', None),
        "external_link_errors": {
            '0': _('Please enter a valid URL.'),
            '1': _('The URL you have entered exceeds the length limit.'),
            '2': _('The IP address you entered is invalid.'),
            '3': _('The port number you entered is invalid.')
        },
        "mobile_upload_url": mobile_upload_url,
        "mobile_upload_qrcode": mobile_upload_qrcode,
        "notebook_templates": notebook_templates,
        "object_qrcode": object_qrcode,
        "object_url": object_url,
        "restore_form": None,
        "version_id": obj.version_id,
        "user_may_grant": user_may_grant,
        "favorite_measurement_actions": favorite_measurement_actions,
        "FileLogEntryType": FileLogEntryType,
        "file_information_form": FileInformationForm(),
        "file_hiding_form": FileHidingForm(),
        "new_schema_available": new_schema_available,
        "related_objects_tree": related_objects_tree,
        "object_publications": object_publications,
        "user_may_link_publication": user_may_link_publication,
        "user_may_use_as_template": user_may_use_as_template,
        "show_dataverse_export": show_dataverse_export,
        "dataverse_url": dataverse_url,
        "publication_form": publication_form,
        "get_object": get_object,
        "get_object_if_current_user_has_read_permissions": get_object_if_current_user_has_read_permissions,
        "get_object_location_assignment": get_object_location_assignment,
        "get_user": get_user,
        "get_location": get_location,
        "PAGE_SIZES": PAGE_SIZES,
        "HORIZONTAL_LABEL_MARGIN": HORIZONTAL_LABEL_MARGIN,
        "VERTICAL_LABEL_MARGIN": VERTICAL_LABEL_MARGIN,
        "mm": mm,
        "object_location_assignments": get_object_location_assignments(object_id),
        "build_object_location_assignment_confirmation_url": build_object_location_assignment_confirmation_url,
        "user_may_assign_location": True,
        "location_form": location_form,
        "project": linked_project,
        "get_project": get_project_if_it_exists,
        "get_action_type": get_action_type,
        "get_action_type_with_translation_in_language": get_action_type_with_translation_in_language,
        "get_instrument_with_translation_in_language": get_instrument_with_translation_in_language,
        "component": obj.component,
        "fed_object_id": obj.fed_object_id,
        "fed_version_id": obj.fed_version_id,
        "get_component": get_component,
        "location_is_fed": {str(loc.id): loc.fed_id is not None for loc in locations_map.values()},
        "user_is_fed": user_is_fed
    }

    # form kwargs
    if action is not None and action.instrument is not None and flask_login.current_user in action.instrument.responsible_users:
        instrument_log_categories = logic.instrument_log_entries.get_instrument_log_categories(action.instrument.id)
        if 'create_instrument_log_entry' in flask.request.form:
            category_ids = []
            for category_id in flask.request.form.getlist('instrument_log_categories'):
                try:
                    if int(category_id) in [category.id for category in instrument_log_categories]:
                        category_ids.append(int(category_id))
                except Exception:
                    pass

    errors = {}
    form_data = {}
    form = ObjectForm()
    if flask.request.method != 'GET' and form.validate_on_submit():
        raw_form_data = {key: flask.request.form.getlist(key) for key in flask.request.form}
        form_data = {k: v[0] for k, v in raw_form_data.items()}

    if not flask.current_app.config["LOAD_OBJECTS_IN_BACKGROUND"]:
        referencable_objects = get_objects_with_permissions(
            user_id=flask_login.current_user.id,
            permissions=Permissions.READ
        )
        if object is not None:
            referencable_objects = [
                referencable_object
                for referencable_object in referencable_objects
                if referencable_object.object_id != object_id
            ]

    else:
        referencable_objects = []

    sorted_actions = get_sorted_actions_for_user(
        user_id=flask_login.current_user.id
    )
    for action in sorted_actions:
        db.session.expunge(action)

    action_type_id_by_action_id = {}
    for action_type in get_action_types():
        for action in get_actions(action_type.id):
            action_type_id_by_action_id[action.id] = action_type.id

    tags = [{'name': tag.name, 'uses': tag.uses} for tag in logic.tags.get_tags()]
    users = get_users(exclude_hidden=True)
    users.sort(key=lambda user: user.id)

    english = get_language(Language.ENGLISH)

    form_kwargs = {
        "errors": errors,
        "errors": errors,
        "form_data": form_data,
        "form": form,
        "referencable_objects": referencable_objects,
        "sorted_actions": sorted_actions,
        "action_type_id_by_action_id": action_type_id_by_action_id,
        "ActionType": models.ActionType,
        "datetime": datetime,
        "tags": tags,
        "users": users,
        "mode": 'edit',
        "languages": get_languages(),
        "ENGLISH": english
    }

    kwargs = {**view_kwargs, **form_kwargs}

    return flask.render_template('objects/inline_edit/inline_edit_base.html', **kwargs)


def get_object_if_current_user_has_read_permissions(object_id, component_uuid=None):
    user_id = flask_login.current_user.id
    if component_uuid is None or component_uuid == flask.current_app.config['FEDERATION_UUID']:
        try:
            permissions = get_user_object_permissions(object_id, user_id)
            if Permissions.READ not in permissions:
                return None
            else:
                return get_object(object_id)
        except ObjectDoesNotExistError:
            return None
    else:
        try:
            component = get_component_by_uuid(component_uuid)
        except ComponentDoesNotExistError:
            return None
        try:
            object = get_fed_object(object_id, component.id)
            permissions = get_user_object_permissions(object.id, user_id)
            if Permissions.READ not in permissions:
                return None
        except ObjectDoesNotExistError:
            return None
        return object


def get_fed_object_if_current_user_has_read_permissions(fed_object_id, component_uuid):
    user_id = flask_login.current_user.id
    component = get_component_by_uuid(component_uuid)
    try:
        fed_object = get_fed_object(fed_object_id, component_id=component.id)
        permissions = get_user_object_permissions(fed_object.id, user_id)
    except ObjectDoesNotExistError:
        return None
    if Permissions.READ not in permissions:
        return None
    return fed_object


@frontend.route('/objects/<int:object_id>', methods=['GET', 'POST'])
@object_permissions_required(Permissions.READ, on_unauthorized=on_unauthorized)
def object(object_id):
    object = get_object(object_id=object_id)

    user_language_id = get_user_language(flask_login.current_user).id
    english = get_language(Language.ENGLISH)

    object_languages = set()
    user_permissions = get_user_object_permissions(object_id=object_id, user_id=flask_login.current_user.id)
    user_may_edit = Permissions.WRITE in user_permissions and object.fed_object_id is None
    user_may_grant = Permissions.GRANT in user_permissions
    if object.action_id is not None:
        user_may_use_as_template = Permissions.READ in get_user_action_permissions(object.action_id, user_id=flask_login.current_user.id)
        action = get_action_with_translation_in_language(object.action_id, user_language_id, use_fallback=True)
        if action.schema != object.schema:
            new_schema_available = True
        else:
            new_schema_available = False
    else:
        action = None
        new_schema_available = False
        user_may_use_as_template = False
    if action and action.type and action.type.enable_related_objects:
        related_objects_tree = logic.object_relationships.build_related_objects_tree(object_id, user_id=flask_login.current_user.id)
    else:
        related_objects_tree = None
    if not user_may_edit and flask.request.args.get('mode', '') == 'edit':
        if object.fed_object_id is not None:
            flask.flash(_('Editing imported objects is not yet supported.'), 'error')
        return flask.abort(403)
    if not user_may_edit and flask.request.args.get('mode', '') == 'upgrade':
        if object.fed_object_id is not None:
            flask.flash(_('Editing imported objects is not yet supported.'), 'error')
        return flask.abort(403)
    if not flask.current_app.config['DISABLE_INLINE_EDIT']:
        if not user_may_edit and flask.request.args.get('mode', '') == 'inline_edit':
            return flask.abort(403)
        if user_may_edit and flask.request.method == 'GET' and flask.request.args.get('mode', '') in {'', 'inline_edit'}:
            return show_inline_edit(object, get_action(object.action_id), related_objects_tree)
    if flask.request.method == 'GET' and flask.request.args.get('mode', '') not in ('edit', 'upgrade'):
        if action is not None:
            instrument = get_instrument_with_translation_in_language(action.instrument_id, user_language_id) if action.instrument else None
            object_type = get_action_type_with_translation_in_language(
                action_type_id=action.type_id,
                language_id=user_language_id
            ).translation.object_name
        else:
            instrument = None
            object_type = None
        object_log_entries = object_log.get_object_log_entries(object_id=object_id, user_id=flask_login.current_user.id)

        dataverse_enabled = bool(flask.current_app.config['DATAVERSE_URL'])
        if dataverse_enabled:
            dataverse_url = logic.dataverse_export.get_dataverse_url(object_id)
            show_dataverse_export = user_may_grant and not dataverse_url
        else:
            dataverse_url = None
            show_dataverse_export = False

        if user_may_edit:
            serializer = itsdangerous.URLSafeTimedSerializer(flask.current_app.config['SECRET_KEY'], salt='mobile-upload')
            token = serializer.dumps([flask_login.current_user.id, object_id])
            mobile_upload_url = flask.url_for('.mobile_file_upload', object_id=object_id, token=token, _external=True)
            mobile_upload_qrcode = generate_qrcode(mobile_upload_url, should_cache=False)
        else:
            mobile_upload_url = None
            mobile_upload_qrcode = None

        object_url = flask.url_for('.object', object_id=object_id, _external=True)
        object_qrcode = generate_qrcode(object_url, should_cache=True)

        readable_location_ids = [
            location.id
            for location in get_locations_with_user_permissions(flask_login.current_user.id, Permissions.READ)
        ]

        location_form = ObjectLocationAssignmentForm()
        locations_map, locations_tree = get_locations_tree()
        locations = [('-1', '—')]
        unvisited_location_ids_prefixes_and_subtrees = [(location_id, '', locations_tree[location_id]) for location_id in locations_tree]
        while unvisited_location_ids_prefixes_and_subtrees:
            location_id, prefix, subtree = unvisited_location_ids_prefixes_and_subtrees.pop(0)
            location = locations_map[location_id]
            if Permissions.READ in readable_location_ids:
                locations.append((str(location_id), prefix + get_location_name(location, include_id=True)))
            prefix = '{}{} / '.format(prefix, get_location_name(location))
            for location_id in sorted(subtree, key=lambda location_id: get_location_name(locations_map[location_id]), reverse=True):
                unvisited_location_ids_prefixes_and_subtrees.insert(0, (location_id, prefix, subtree[location_id]))

        location_form.location.choices = locations
        possible_responsible_users = [('-1', '—')]
        user_is_fed = {}
        for user in logic.users.get_users(exclude_hidden=True):
            possible_responsible_users.append((str(user.id), user.get_name()))
            user_is_fed[str(user.id)] = user.fed_id is not None
        location_form.responsible_user.choices = possible_responsible_users

        measurement_actions = logic.action_translations.get_actions_with_translation_in_language(user_language_id, models.ActionType.MEASUREMENT, use_fallback=True)
        favorite_action_ids = logic.favorites.get_user_favorite_action_ids(flask_login.current_user.id)
        favorite_measurement_actions = [
            action
            for action in measurement_actions
            if action.id in favorite_action_ids and not action.is_hidden
        ]
        # Sort by: instrument name (independent actions first), action name
        favorite_measurement_actions.sort(key=lambda action: (
            action.user.name.lower() if action.user else '',
            get_instrument_with_translation_in_language(action.instrument_id, user_language_id).translation.name.lower() if action.instrument else '',
            action.translation.name.lower()
        ))

        publication_form = ObjectPublicationForm()

        object_publications = logic.publications.get_publications_for_object(object_id=object.id)
        user_may_link_publication = Permissions.WRITE in user_permissions

        if object.schema is not None and object.data is not None:
            notebook_templates = get_notebook_templates(
                object_id=object.id,
                data=object.data,
                schema=object.schema,
                user_id=flask_login.current_user.id
            )
        else:
            notebook_templates = []

        linked_project = logic.projects.get_project_linked_to_object(object_id)

        object_languages = logic.languages.get_languages_in_object_data(object.data)
        languages = []
        for lang_code in object_languages:
            languages.append(get_language_by_lang_code(lang_code))

        all_languages = get_languages()
        metadata_language = flask.request.args.get('language', None)
        if not any(
            language.lang_code == metadata_language
            for language in languages
        ):
            metadata_language = None

        if object.user_id is not None:
            last_edit_user = get_user(object.user_id)
        else:
            last_edit_user = None

        return flask.render_template(
            'objects/view/base.html',
            template_mode="view",
            show_object_type_and_id_on_object_page_text=get_user_settings(flask_login.current_user.id)["SHOW_OBJECT_TYPE_AND_ID_ON_OBJECT_PAGE"],
            show_object_title=get_user_settings(flask_login.current_user.id)["SHOW_OBJECT_TITLE"],
            measurement_type_name=logic.action_type_translations.get_action_type_translation_for_action_type_in_language(
                action_type_id=logic.actions.models.ActionType.MEASUREMENT,
                language_id=logic.languages.get_user_language(flask_login.current_user).id,
                use_fallback=True
            ).name,
            metadata_language=metadata_language,
            languages=languages,
            all_languages=all_languages,
            SUPPORTED_LOCALES=logic.locale.SUPPORTED_LOCALES,
            ENGLISH=english,
            object_type=object_type,
            action=action,
            action_type=get_action_type_with_translation_in_language(action.type_id, user_language_id) if action is not None else None,
            instrument=instrument,
            schema=object.schema,
            data=object.data,
            name=object.name,
            object_log_entries=object_log_entries,
            ObjectLogEntryType=ObjectLogEntryType,
            last_edit_datetime=object.utc_datetime,
            last_edit_user=last_edit_user,
            object_id=object_id,
            user_may_edit=user_may_edit,
            user_may_comment=user_may_edit,
            comments=comments.get_comments_for_object(object_id),
            comment_form=CommentForm(),
            files=logic.files.get_files_for_object(object_id),
            file_source_instrument_exists=False,
            file_source_jupyterhub_exists=False,
            file_form=FileForm(),
            external_link_form=ExternalLinkForm(),
            external_link_error=flask.request.args.get('invalid_link_error', None),
            external_link_errors={
                '0': _('Please enter a valid URL.'),
                '1': _('The URL you have entered exceeds the length limit.'),
                '2': _('The IP address you entered is invalid.'),
                '3': _('The port number you entered is invalid.')
            },
            mobile_upload_url=mobile_upload_url,
            mobile_upload_qrcode=mobile_upload_qrcode,
            notebook_templates=notebook_templates,
            object_qrcode=object_qrcode,
            object_url=object_url,
            restore_form=None,
            version_id=object.version_id,
            user_may_grant=user_may_grant,
            favorite_measurement_actions=favorite_measurement_actions,
            FileLogEntryType=FileLogEntryType,
            file_information_form=FileInformationForm(),
            file_hiding_form=FileHidingForm(),
            new_schema_available=new_schema_available,
            related_objects_tree=related_objects_tree,
            object_publications=object_publications,
            user_may_link_publication=user_may_link_publication,
            user_may_use_as_template=user_may_use_as_template,
            show_dataverse_export=show_dataverse_export,
            dataverse_url=dataverse_url,
            publication_form=publication_form,
            get_object=get_object,
            get_object_if_current_user_has_read_permissions=get_object_if_current_user_has_read_permissions,
            get_fed_object_if_current_user_has_read_permissions=get_fed_object_if_current_user_has_read_permissions,
            get_object_location_assignment=get_object_location_assignment,
            get_user=get_user_if_exists,
            get_location=get_location,
            PAGE_SIZES=PAGE_SIZES,
            HORIZONTAL_LABEL_MARGIN=HORIZONTAL_LABEL_MARGIN,
            VERTICAL_LABEL_MARGIN=VERTICAL_LABEL_MARGIN,
            mm=mm,
            object_location_assignments=get_object_location_assignments(object_id),
            build_object_location_assignment_confirmation_url=build_object_location_assignment_confirmation_url,
            user_may_assign_location=user_may_edit,
            location_form=location_form,
            project=linked_project,
            get_project=get_project_if_it_exists,
            get_action_type=get_action_type,
            get_action_type_with_translation_in_language=get_action_type_with_translation_in_language,
            get_instrument_with_translation_in_language=get_instrument_with_translation_in_language,
            component=object.component,
            fed_object_id=object.fed_object_id,
            fed_version_id=object.fed_version_id,
            get_component=get_component,
            location_is_fed={str(loc.id): loc.fed_id is not None for loc in locations_map.values()},
            user_is_fed=user_is_fed
        )
    check_current_user_is_not_readonly()
    if flask.request.args.get('mode', '') == 'upgrade':
        should_upgrade_schema = True
    else:
        should_upgrade_schema = False
    return show_object_form(object, action=get_action(object.action_id), should_upgrade_schema=should_upgrade_schema)


@frontend.route('/objects/<int:object_id>/dc.rdf')
@frontend.route('/objects/<int:object_id>/versions/<int:version_id>/dc.rdf')
@object_permissions_required(Permissions.READ, on_unauthorized=on_unauthorized)
def object_rdf(object_id, version_id=None):
    rdf_xml = logic.rdf.generate_rdf(flask_login.current_user.id, object_id, version_id)
    return flask.Response(
        rdf_xml,
        mimetype='application/rdf+xml',
    )


@frontend.route('/objects/<int:object_id>/label')
@object_permissions_required(Permissions.READ, on_unauthorized=on_unauthorized)
def print_object_label(object_id):
    mode = flask.request.args.get('mode', 'mixed')
    if mode == 'fixed-width':
        create_mixed_labels = False
        create_long_labels = False
        include_qrcode_in_long_labels = None
        paper_format = flask.request.args.get('width-paper-format', '')
        if paper_format not in PAGE_SIZES:
            paper_format = DEFAULT_PAPER_FORMAT
        maximum_width = math.floor(PAGE_SIZES[paper_format][0] / mm - 2 * HORIZONTAL_LABEL_MARGIN)
        maximum_height = math.floor(PAGE_SIZES[paper_format][1] / mm - 2 * VERTICAL_LABEL_MARGIN)
        ghs_classes_side_by_side = 'side-by-side' in flask.request.args
        label_minimum_width = 20
        if ghs_classes_side_by_side:
            label_minimum_width = 40
        try:
            label_width = float(flask.request.args.get('label-width', '20'))
        except ValueError:
            label_width = 0
        if math.isnan(label_width):
            label_width = 0
        if label_width < label_minimum_width:
            label_width = label_minimum_width
        if label_width > maximum_width:
            label_width = maximum_width
        try:
            label_minimum_height = float(flask.request.args.get('label-minimum-height', '0'))
        except ValueError:
            label_minimum_height = 0
        if math.isnan(label_minimum_height):
            label_minimum_height = 0
        if label_minimum_height < 0:
            label_minimum_height = 0
        if label_minimum_height > maximum_height:
            label_minimum_height = maximum_height
        qrcode_width = 18
        centered = 'centered' in flask.request.args
    elif mode == 'minimum-height':
        create_mixed_labels = False
        create_long_labels = True
        paper_format = flask.request.args.get('height-paper-format', '')
        if paper_format not in PAGE_SIZES:
            paper_format = DEFAULT_PAPER_FORMAT
        maximum_width = math.floor(PAGE_SIZES[paper_format][0] / mm - 2 * HORIZONTAL_LABEL_MARGIN)
        include_qrcode_in_long_labels = 'include-qrcode' in flask.request.args
        label_width = 0
        label_minimum_height = 0
        try:
            label_minimum_width = float(flask.request.args.get('label-minimum-width', '0'))
        except ValueError:
            label_minimum_width = 0
        if math.isnan(label_minimum_width):
            label_minimum_width = 0
        if label_minimum_width < 0:
            label_minimum_width = 0
        if label_minimum_width > maximum_width:
            label_minimum_width = maximum_width
        qrcode_width = 0
        ghs_classes_side_by_side = None
        centered = None
    else:
        create_mixed_labels = True
        create_long_labels = None
        include_qrcode_in_long_labels = None
        paper_format = flask.request.args.get('mixed-paper-format', '')
        if paper_format not in PAGE_SIZES:
            paper_format = DEFAULT_PAPER_FORMAT
        label_width = 0
        label_minimum_height = 0
        qrcode_width = 0
        label_minimum_width = 0
        ghs_classes_side_by_side = None
        centered = None

    object = get_object(object_id=object_id)
    object_log_entries = object_log.get_object_log_entries(object_id=object_id, user_id=flask_login.current_user.id)
    for object_log_entry in object_log_entries:
        if object_log_entry.type in (ObjectLogEntryType.CREATE_OBJECT, ObjectLogEntryType.CREATE_BATCH):
            creation_date = object_log_entry.utc_datetime.strftime('%Y-%m-%d')
            creation_user = get_user(object_log_entry.user_id).name
            break
    else:
        creation_date = _('Unknown')
        creation_user = _('Unknown')
    if 'created' in object.data and '_type' in object.data['created'] and object.data['created']['_type'] == 'datetime':
        creation_date = object.data['created']['utc_datetime'].split(' ')[0]
    object_name = get_translated_text(object.name)

    object_url = flask.url_for('.object', object_id=object_id, _external=True)

    if 'hazards' in object.data and '_type' in object.data['hazards'] and object.data['hazards']['_type'] == 'hazards':
        hazards = object.data['hazards']['hazards']
    else:
        hazards = []

    pdf_data = create_labels(
        object_id=object_id,
        object_name=object_name,
        object_url=object_url,
        creation_user=creation_user,
        creation_date=creation_date,
        ghs_classes=hazards,
        paper_format=paper_format,
        create_mixed_labels=create_mixed_labels,
        create_long_labels=create_long_labels,
        include_qrcode_in_long_labels=include_qrcode_in_long_labels,
        label_width=label_width,
        label_minimum_height=label_minimum_height,
        label_minimum_width=label_minimum_width,
        qrcode_width=qrcode_width,
        ghs_classes_side_by_side=ghs_classes_side_by_side,
        centered=centered
    )
    return flask.send_file(
        io.BytesIO(pdf_data),
        mimetype='application/pdf',
        max_age=-1
    )


@frontend.route('/objects/<int:object_id>/comments/', methods=['POST'])
@object_permissions_required(Permissions.WRITE)
def post_object_comments(object_id):
    check_current_user_is_not_readonly()
    comment_form = CommentForm()
    if comment_form.validate_on_submit():
        content = comment_form.content.data
        comments.create_comment(object_id=object_id, user_id=flask_login.current_user.id, content=content)
        flask.flash(_('Successfully posted a comment.'), 'success')
    else:
        flask.flash(_('Please enter a comment text.'), 'error')
    return flask.redirect(flask.url_for('.object', object_id=object_id))


@frontend.route('/objects/search/')
@flask_login.login_required
def search():
    actions = get_sorted_actions_for_user(
        user_id=flask_login.current_user.id
    )
    user_language_id = get_user_language(flask_login.current_user).id

    action_types = get_action_types_with_translations_in_language(user_language_id, filter_fed_defaults=True)

    search_paths, search_paths_by_action, search_paths_by_action_type = get_search_paths(actions, action_types)

    if not flask.current_app.config["LOAD_OBJECTS_IN_BACKGROUND"]:
        referencable_objects = get_objects_with_permissions(
            user_id=flask_login.current_user.id,
            permissions=Permissions.READ
        )
    else:
        referencable_objects = []
    return flask.render_template(
        'search.html',
        search_paths=search_paths,
        search_paths_by_action=search_paths_by_action,
        search_paths_by_action_type=search_paths_by_action_type,
        actions=actions,
        action_types=action_types,
        datetime=datetime,
        users=get_users(exclude_hidden=True),
        referencable_objects=referencable_objects
    ), 200, {
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
    }


@frontend.route('/objects/referencable')
@flask_login.login_required
def referencable_objects():
    required_perm = Permissions.READ
    if 'required_perm' in flask.request.args:
        try:
            required_perm = Permissions.from_name(flask.request.args['required_perm'])
        except ValueError:
            try:
                required_perm = Permissions(int(flask.request.args['required_perm']))
            except ValueError:
                return {
                    "message": "argument {} is not a valid permission.".format(flask.request.args['required_perm'])
                }, 400

    action_ids = None
    if 'action_ids' in flask.request.args:
        action_ids = flask.request.args['action_ids']
        try:
            action_ids = json.loads(action_ids)
        except Exception:
            action_ids = None
        else:
            if type(action_ids) is not list:
                action_ids = None
            elif -1 in action_ids:
                action_ids = None
            elif not all(type(action_id) is int for action_id in action_ids):
                action_ids = None

    referencable_objects = get_object_info_with_permissions(
        user_id=flask_login.current_user.id,
        permissions=required_perm,
        action_ids=action_ids
    )

    def dictify(x):
        return {
            'id': x.object_id,
            'text': '{} (#{})'.format(flask.escape(get_translated_text(x.name_json)) or '&mdash;', x.object_id) if x.component_name is None
            else '{} (#{}, #{} @ {})'.format(flask.escape(get_translated_text(x.name_json)) or '&mdash;', x.object_id, x.fed_object_id, flask.escape(x.component_name)),
            'action_id': x.action_id,
            'max_permission': x.max_permission,
            'tags': [flask.escape(tag) for tag in x.tags['tags']] if x.tags and isinstance(x.tags, dict) and x.tags.get('_type') == 'tags' and x.tags.get('tags') else [],
            'is_fed': x.fed_object_id is not None
        }

    return {
        'referencable_objects': [
            dictify(object)
            for object in referencable_objects
        ]
    }


@frontend.route('/objects/<int:object_id>/permissions/request', methods=['POST'])
@flask_login.login_required
def object_permissions_request(object_id):
    current_permissions = get_user_object_permissions(object_id=object_id, user_id=flask_login.current_user.id)
    if Permissions.READ in current_permissions:
        flask.flash(_('You already have permissions to access this object.'), 'error')
        return flask.redirect(flask.url_for('.object', object_id=object_id))
    request_object_permissions(flask_login.current_user.id, object_id)
    flask.flash(_('Your request for permissions has been sent.'), 'success')
    return flask.redirect(flask.url_for('.objects'))


@frontend.route('/objects/<int:object_id>/locations/', methods=['POST'])
@object_permissions_required(Permissions.WRITE)
def post_object_location(object_id):
    check_current_user_is_not_readonly()
    location_form = ObjectLocationAssignmentForm()
    location_form.location.choices = [('-1', '—')] + [
        (str(location.id), get_location_name(location, include_id=True))
        for location in get_locations_with_user_permissions(flask_login.current_user.id, Permissions.READ)
    ]
    possible_responsible_users = [('-1', '—')]
    for user in logic.users.get_users(exclude_hidden=True):
        possible_responsible_users.append((str(user.id), '{} (#{})'.format(user.name, user.id)))
    location_form.responsible_user.choices = possible_responsible_users
    if location_form.validate_on_submit():
        location_id = int(location_form.location.data)
        if location_id < 0:
            location_id = None
        responsible_user_id = int(location_form.responsible_user.data)
        if responsible_user_id < 0:
            responsible_user_id = None
        description = location_form.description.data
        try:
            description = json.loads(description)
        except Exception:
            description = {}
        valid_description = {'en': ''}
        for language_code, description_text in description.items():
            if not isinstance(language_code, str):
                continue
            try:
                language = get_language_by_lang_code(language_code)
            except logic.errors.LanguageDoesNotExistError:
                continue
            if not language.enabled_for_input:
                continue
            valid_description[language_code] = description_text
        description = valid_description
        if location_id is not None or responsible_user_id is not None:
            assign_location_to_object(object_id, location_id, responsible_user_id, flask_login.current_user.id, description)
            flask.flash(_('Successfully assigned a new location to this object.'), 'success')
        else:
            flask.flash(_('Please select a location or a responsible user.'), 'error')
    else:
        flask.flash(_('Please select a location or a responsible user.'), 'error')
    return flask.redirect(flask.url_for('.object', object_id=object_id))


@frontend.route('/objects/<int:object_id>/publications/', methods=['POST'])
@object_permissions_required(Permissions.WRITE)
def post_object_publication(object_id):
    check_current_user_is_not_readonly()
    publication_form = ObjectPublicationForm()
    if publication_form.validate_on_submit():
        doi = publication_form.doi.data
        title = publication_form.title.data
        object_name = publication_form.object_name.data
        if title is not None:
            title = title.strip()
        if not title:
            title = None
        if object_name is not None:
            object_name = object_name.strip()
        if not object_name:
            object_name = None
        existing_publication = ([
            publication
            for publication in logic.publications.get_publications_for_object(object_id)
            if publication.doi == doi
        ] or [None])[0]
        if existing_publication is not None and existing_publication.title == title and existing_publication.object_name == object_name:
            flask.flash(_('This object has already been linked to this publication.'), 'info')
        else:
            logic.publications.link_publication_to_object(user_id=flask_login.current_user.id, object_id=object_id, doi=doi, title=title, object_name=object_name)
            if existing_publication is None:
                flask.flash(_('Successfully linked this object to a publication.'), 'success')
            else:
                flask.flash(_('Successfully updated the information for this publication.'), 'success')
    else:
        flask.flash(_('Please enter a valid DOI for the publication you want to link this object to.'), 'error')
    return flask.redirect(flask.url_for('.object', object_id=object_id))


@frontend.route('/objects/<int:object_id>/export')
@object_permissions_required(Permissions.READ, on_unauthorized=on_unauthorized)
def export_data(object_id):
    object_ids = [object_id]
    file_extension = flask.request.args.get('format', '.pdf')
    if file_extension != '.pdf' and file_extension not in logic.export.FILE_FORMATS:
        return flask.abort(400)
    if 'object_ids' in flask.request.args:
        try:
            object_ids = json.loads(flask.request.args['object_ids'])
            object_ids = [int(i) for i in object_ids]
            if any((Permissions.READ not in get_user_object_permissions(i, flask_login.current_user.id)) for i in object_ids):
                return flask.abort(400)
        except Exception:
            return flask.abort(400)
        if not object_ids:
            return flask.abort(400)
    if file_extension == '.pdf':
        sections = pdfexport.SECTIONS
        if 'sections' in flask.request.args:
            try:
                sections = sections.intersection(json.loads(flask.request.args['sections']))
            except Exception:
                return flask.abort(400)
        if 'language' in flask.request.args:
            try:
                lang_code = flask.request.args['language']
                if lang_code not in logic.locale.SUPPORTED_LOCALES:
                    raise ValueError()
                language = logic.languages.get_language_by_lang_code(lang_code)
                if not language.enabled_for_user_interface:
                    raise ValueError()
            except Exception:
                lang_code = 'en'
        else:
            lang_code = 'en'
        pdf_data = pdfexport.create_pdfexport(object_ids, sections, lang_code)
        file_bytes = io.BytesIO(pdf_data)
    elif file_extension in logic.export.FILE_FORMATS:
        file_bytes = logic.export.FILE_FORMATS[file_extension][1](flask_login.current_user.id, object_ids=object_ids)
    else:
        file_bytes = None
    if file_bytes:
        return flask.Response(
            file_bytes,
            200,
            headers={
                'Content-Disposition': f'attachment; filename=sampledb_export{file_extension}',
                'Content-Type': 'application/pdf' if file_extension == '.pdf' else logic.export.FILE_FORMATS[file_extension][2]
            }
        )
    return flask.abort(500)


@frontend.route('/objects/<int:object_id>/files/')
@object_permissions_required(Permissions.READ, on_unauthorized=on_unauthorized)
def object_files(object_id):
    files = logic.files.get_files_for_object(object_id)
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, 'w') as zip_file:
        for file in files:
            if file.is_hidden:
                continue
            if file.storage in {'local', 'database'}:
                try:
                    file_bytes = file.open(read_only=True).read()
                except Exception:
                    pass
                else:
                    zip_file.writestr(os.path.basename(file.original_file_name), file_bytes)
    return flask.Response(
        zip_bytes.getvalue(),
        200,
        headers={
            'Content-Type': 'application/zip',
            'Content-Disposition': f'attachment; filename=object_{object_id}_files.zip'
        }
    )


@frontend.route('/objects/<int:object_id>/files/<int:file_id>', methods=['GET'])
@object_permissions_required(Permissions.READ, on_unauthorized=on_unauthorized)
def object_file(object_id, file_id):
    file = logic.files.get_file_for_object(object_id, file_id)
    if file is None:
        return flask.abort(404)
    if file.is_hidden:
        return flask.abort(403)
    if file.storage in ('local', 'database'):
        if 'preview' in flask.request.args:
            file_extension = os.path.splitext(file.original_file_name)[1]
            mime_type = flask.current_app.config.get('MIME_TYPES', {}).get(file_extension, None)
            if mime_type is not None:
                return flask.send_file(file.open(), mimetype=mime_type, last_modified=file.utc_datetime)
        return flask.send_file(file.open(), as_attachment=True, download_name=file.original_file_name, last_modified=file.utc_datetime)
    # TODO: better error handling
    return flask.abort(404)


@frontend.route('/objects/<int:object_id>/files/<int:file_id>', methods=['POST'])
@object_permissions_required(Permissions.WRITE)
def update_file_information(object_id, file_id):
    check_current_user_is_not_readonly()
    form = FileInformationForm()
    if not form.validate_on_submit():
        return flask.abort(400)
    title = form.title.data
    description = form.description.data
    try:
        logic.files.update_file_information(
            object_id=object_id,
            file_id=file_id,
            user_id=flask_login.current_user.id,
            title=title,
            description=description
        )
    except logic.errors.FileDoesNotExistError:
        return flask.abort(404)
    return flask.redirect(flask.url_for('.object', object_id=object_id, _anchor='file-{}'.format(file_id)))


@frontend.route('/objects/<int:object_id>/files/<int:file_id>/hide', methods=['POST'])
@object_permissions_required(Permissions.WRITE)
def hide_file(object_id, file_id):
    check_current_user_is_not_readonly()
    form = FileHidingForm()
    if not form.validate_on_submit():
        return flask.abort(400)
    reason = form.reason.data
    try:
        logic.files.hide_file(
            object_id=object_id,
            file_id=file_id,
            user_id=flask_login.current_user.id,
            reason=reason
        )
    except logic.errors.FileDoesNotExistError:
        return flask.abort(404)
    flask.flash(_('The file was hidden successfully.'), 'success')
    return flask.redirect(flask.url_for('.object', object_id=object_id, _anchor='file-{}'.format(file_id)))


@frontend.route('/objects/<int:object_id>/files/mobile_upload/<token>', methods=['GET'])
def mobile_file_upload(object_id: int, token: str):
    serializer = itsdangerous.URLSafeTimedSerializer(flask.current_app.config['SECRET_KEY'], salt='mobile-upload')
    try:
        user_id, object_id = serializer.loads(token, max_age=15 * 60)
    except itsdangerous.BadSignature:
        return flask.abort(400)
    try:
        user = logic.users.get_user(user_id)
    except UserDoesNotExistError:
        return flask.abort(403)
    if user.is_readonly:
        return flask.abort(403)
    return flask.render_template('mobile_upload.html')


@frontend.route('/objects/<int:object_id>/files/mobile_upload/<token>', methods=['POST'])
def post_mobile_file_upload(object_id: int, token: str):
    serializer = itsdangerous.URLSafeTimedSerializer(flask.current_app.config['SECRET_KEY'], salt='mobile-upload')
    try:
        user_id, object_id = serializer.loads(token, max_age=15 * 60)
    except itsdangerous.BadSignature:
        return flask.abort(400)
    try:
        user = logic.users.get_user(user_id)
    except UserDoesNotExistError:
        return flask.abort(403)
    if user.is_readonly:
        return flask.abort(403)
    files = flask.request.files.getlist('file_input')
    if not files:
        return flask.redirect(
            flask.url_for(
                '.mobile_file_upload',
                object_id=object_id,
                token=token
            )
        )
    for file_storage in files:
        file_name = werkzeug.utils.secure_filename(file_storage.filename)
        logic.files.create_database_file(object_id, user_id, file_name, lambda stream: file_storage.save(dst=stream))
    return flask.render_template('mobile_upload_success.html')


@frontend.route('/objects/<int:object_id>/files/', methods=['POST'])
@object_permissions_required(Permissions.WRITE)
def post_object_files(object_id):
    check_current_user_is_not_readonly()
    external_link_form = ExternalLinkForm()
    file_form = FileForm()
    if file_form.validate_on_submit():
        file_source = file_form.file_source.data
        if file_source == 'local':
            files = flask.request.files.getlist(file_form.local_files.name)
            for file_storage in files:
                file_name = werkzeug.utils.secure_filename(file_storage.filename)
                logic.files.create_database_file(object_id, flask_login.current_user.id, file_name, lambda stream: file_storage.save(dst=stream))
            flask.flash(_('Successfully uploaded files.'), 'success')
        else:
            flask.flash(_('Failed to upload files.'), 'error')
    elif external_link_form.validate_on_submit():
        url = external_link_form.url.data
        logic.files.create_url_file(object_id, flask_login.current_user.id, url)
        flask.flash(_('Successfully posted link.'), 'success')
    elif external_link_form.errors:
        errorcode = 1
        if 'url' in external_link_form.errors:
            errorcode = external_link_form.errors['url']

        flask.flash(_('Failed to post link.'), 'error')
        return flask.redirect(flask.url_for('.object', object_id=object_id, invalid_link_error=errorcode, _anchor='anchor-post-link'))
    else:
        flask.flash(_('Failed to upload files.'), 'error')
    return flask.redirect(flask.url_for('.object', object_id=object_id))


@frontend.route('/objects/new', methods=['GET', 'POST'])
@flask_login.login_required
def new_object():
    check_current_user_is_not_readonly()
    action_id = flask.request.args.get('action_id', None)
    previous_object_id = flask.request.args.get('previous_object_id', None)
    if not action_id and not previous_object_id:
        # TODO: handle error
        return flask.abort(404)

    sample_id = flask.request.args.get('sample_id', None)

    previous_object = None
    action = None
    if previous_object_id:
        try:
            previous_object = get_object(previous_object_id)
        except ObjectDoesNotExistError:
            flask.flash(_("This object does not exist."), 'error')
            return flask.abort(404)
        if Permissions.READ not in get_user_object_permissions(user_id=flask_login.current_user.id, object_id=previous_object_id):
            flask.flash(_("You do not have the required permissions to use this object as a template."), 'error')
            return flask.abort(403)
        if action_id:
            if action_id != str(previous_object.action_id):
                flask.flash(_("This object was created with a different action."), 'error')
                return flask.abort(400)
            else:
                action_id = previous_object.action_id
    if action_id:
        try:
            action = get_action(action_id)
        except ActionDoesNotExistError:
            flask.flash(_("This action does not exist."), 'error')
            return flask.abort(404)
        if Permissions.READ not in get_user_action_permissions(action_id, user_id=flask_login.current_user.id):
            flask.flash(_("You do not have the required permissions to use this action."), 'error')
            return flask.abort(403)

    placeholder_data = {}

    if sample_id is not None:
        try:
            sample_id = int(sample_id)
        except ValueError:
            sample_id = None
        else:
            if sample_id <= 0:
                sample_id = None
    if sample_id is not None:
        try:
            logic.objects.get_object(sample_id)
        except logic.errors.ObjectDoesNotExistError:
            sample_id = None
    if sample_id is not None:
        if action.schema.get('properties', {}).get('sample', {}).get('type', '') == 'sample':
            placeholder_data = {
                ('sample', ): {'_type': 'sample', 'object_id': sample_id}
            }

    # TODO: check instrument permissions
    return show_object_form(None, action, previous_object, placeholder_data=placeholder_data)


@frontend.route('/objects/<int:object_id>/versions/')
@object_permissions_required(Permissions.READ, on_unauthorized=on_unauthorized)
def object_versions(object_id):
    object = get_object(object_id=object_id)
    if object is None:
        return flask.abort(404)
    object_versions = get_object_versions(object_id=object_id)
    object_versions.sort(key=lambda object_version: -object_version.version_id)
    return flask.render_template('objects/object_versions.html', get_user=get_user_if_exists, object=object, object_versions=object_versions)


@frontend.route('/objects/<int:object_id>/versions/<int:version_id>')
@object_permissions_required(Permissions.READ, on_unauthorized=on_unauthorized)
def object_version(object_id, version_id):
    user_language_id = logic.languages.get_user_language(flask_login.current_user).id
    english = get_language(Language.ENGLISH)
    object = get_object(object_id=object_id, version_id=version_id)
    form = None
    user_permissions = get_user_object_permissions(object_id=object_id, user_id=flask_login.current_user.id)
    if Permissions.WRITE in user_permissions:
        current_object = get_object(object_id=object_id)
        if current_object.version_id != version_id:
            form = ObjectVersionRestoreForm()
    user_may_grant = Permissions.GRANT in user_permissions
    action = get_action_with_translation_in_language(object.action_id, user_language_id, use_fallback=True)
    action_type = get_action_type_with_translation_in_language(action.type_id, user_language_id)
    instrument = get_instrument_with_translation_in_language(action.instrument_id, user_language_id) if action.instrument_id else None

    object_languages = logic.languages.get_languages_in_object_data(object.data)
    languages = []
    for lang_code in object_languages:
        languages.append(get_language_by_lang_code(lang_code))

    metadata_language = flask.request.args.get('language', None)
    if not any(
        language.lang_code == metadata_language
        for language in languages
    ):
        metadata_language = None
    return flask.render_template(
        'objects/view/base.html',
        template_mode="view",
        show_object_type_and_id_on_object_page_text=get_user_settings(flask_login.current_user.id)["SHOW_OBJECT_TYPE_AND_ID_ON_OBJECT_PAGE"],
        show_object_title=get_user_settings(flask_login.current_user.id)["SHOW_OBJECT_TITLE"],
        languages=languages,
        metadata_language=metadata_language,
        ENGLISH=english,
        is_archived=True,
        object_type=action_type.translation.object_name,
        action=action,
        action_type=action_type,
        instrument=instrument,
        schema=object.schema,
        data=object.data,
        name=object.name,
        last_edit_datetime=object.utc_datetime,
        last_edit_user=get_user(object.user_id),
        get_object_if_current_user_has_read_permissions=get_object_if_current_user_has_read_permissions,
        object_id=object_id,
        version_id=version_id,
        link_version_specific_rdf=True,
        restore_form=form,
        get_user=get_user_if_exists,
        user_may_grant=user_may_grant,
        get_action_type=get_action_type,
        get_action_type_with_translation_in_language=get_action_type_with_translation_in_language,
        component=object.component,
        fed_object_id=object.fed_object_id,
        fed_version_id=object.fed_version_id,
        get_component=get_component
    )


@frontend.route('/objects/<int:object_id>/versions/<int:version_id>/restore', methods=['GET', 'POST'])
@object_permissions_required(Permissions.WRITE)
def restore_object_version(object_id, version_id):
    if version_id < 0 or object_id < 0:
        return flask.abort(404)
    try:
        current_object = get_object(object_id=object_id)
    except ObjectDoesNotExistError:
        return flask.abort(404)
    if current_object.version_id <= version_id:
        return flask.abort(404)
    form = ObjectVersionRestoreForm()
    if form.validate_on_submit():
        try:
            logic.objects.restore_object_version(object_id=object_id, version_id=version_id, user_id=flask_login.current_user.id)
        except logic.errors.ValidationError:
            flask.flash(_('This version contains invalid data and cannot be restored.'), 'error')
        else:
            return flask.redirect(flask.url_for('.object', object_id=object_id))
    return flask.render_template('objects/restore_object_version.html', object_id=object_id, version_id=version_id, restore_form=form)


@frontend.route('/objects/<int:object_id>/permissions')
@object_permissions_required(Permissions.READ, on_unauthorized=on_unauthorized)
def object_permissions(object_id):
    check_current_user_is_not_readonly()
    object = get_object(object_id)
    components = get_components()
    if object.action_id is not None:
        action = get_action(object.action_id)
        instrument = action.instrument
    else:
        action = None
        instrument = None
    user_permissions = get_object_permissions_for_users(object_id=object_id, include_instrument_responsible_users=False, include_groups=False, include_projects=False, include_readonly=False, include_admin_permissions=False)
    group_permissions = get_object_permissions_for_groups(object_id=object_id, include_projects=False)
    project_permissions = get_object_permissions_for_projects(object_id=object_id)
    all_user_permissions = get_object_permissions_for_all_users(object_id=object_id)
    component_policies = {share.component_id: share for share in get_shares_for_object(object_id)}
    policies = {share.component_id: share.policy for share in get_shares_for_object(object_id)}
    suggested_user_id = flask.request.args.get('add_user_id', '')
    try:
        suggested_user_id = int(suggested_user_id)
    except ValueError:
        suggested_user_id = None
    if Permissions.GRANT in get_user_object_permissions(object_id=object_id, user_id=flask_login.current_user.id):
        (
            add_user_permissions_form,
            add_group_permissions_form,
            add_project_permissions_form,
            permissions_form
        ) = set_up_permissions_forms(
            logic.object_permissions.object_permissions,
            object_id,
            all_user_permissions,
            user_permissions,
            group_permissions,
            project_permissions
        )

        users = get_users(exclude_hidden=True, exclude_fed=True)
        users = [user for user in users if user.id not in user_permissions]
        users.sort(key=lambda user: user.id)

        groups = get_user_groups(flask_login.current_user.id)
        groups = [group for group in groups if group.id not in group_permissions]
        groups.sort(key=lambda group: group.id)

        projects = get_user_projects(flask_login.current_user.id, include_groups=True)
        projects = [project for project in projects if project.id not in project_permissions]
        projects.sort(key=lambda project: project.id)

        possible_new_components = [component for component in components if component.id not in component_policies.keys()]
        component_users = {
            component.id: {
                user.fed_id: user.get_name(include_ref=True)
                for user in get_users_for_component(component.id, exclude_hidden=True)
            }
            for component in components
        }
        add_component_policy_form = ObjectNewShareAccessForm()
        add_component_policy_form.data.data = True
        add_component_policy_form.action.data = True
        add_component_policy_form.users.data = True
        add_component_policy_form.files.data = True
        add_component_policy_form.comments.data = True
        add_component_policy_form.object_location_assignments.data = True
        edit_component_policy_form = ObjectEditShareAccessForm()
        edit_component_policy_form.data.data = True
        edit_component_policy_form.action.data = True
        edit_component_policy_form.users.data = True
        edit_component_policy_form.files.data = True
        edit_component_policy_form.comments.data = True
        edit_component_policy_form.object_location_assignments.data = True
        copy_permissions_form = CopyPermissionsForm()
        if not flask.current_app.config["LOAD_OBJECTS_IN_BACKGROUND"]:
            existing_objects = get_objects_with_permissions(
                user_id=flask_login.current_user.id,
                permissions=Permissions.GRANT
            )
            copy_permissions_form.object_id.choices = [
                (str(existing_object.id), existing_object.name)
                for existing_object in existing_objects
                if existing_object.id != object_id
            ]
            if len(copy_permissions_form.object_id.choices) == 0:
                copy_permissions_form = None
        else:
            copy_permissions_form.object_id.choices = []
    else:
        permissions_form = None
        add_user_permissions_form = None
        add_group_permissions_form = None
        add_project_permissions_form = None
        add_component_policy_form = None
        copy_permissions_form = None
        edit_component_policy_form = None

        users = []
        groups = []
        projects = []
        possible_new_components = None
        component_users = []

    acceptable_project_ids = {
        project.id
        for project in projects
    }

    all_projects = logic.projects.get_projects()
    all_projects_by_id = {
        project.id: project
        for project in all_projects
    }

    if not flask.current_app.config['DISABLE_SUBPROJECTS']:
        project_id_hierarchy_list = logic.projects.get_project_id_hierarchy_list(list(all_projects_by_id))
        project_id_hierarchy_list = [
            (level, project_id, project_id in acceptable_project_ids)
            for level, project_id in project_id_hierarchy_list
        ]
    else:
        project_id_hierarchy_list = [
            (0, project.id, project.id in acceptable_project_ids)
            for project in sorted(all_projects, key=lambda project: project.id)
        ]
    show_projects_form = any(
        enabled
        for level, project_id, enabled in project_id_hierarchy_list
    )
    return flask.render_template(
        'objects/object_permissions.html',
        instrument=instrument,
        action=action,
        object=object,
        user_permissions=user_permissions,
        group_permissions=group_permissions,
        project_permissions=project_permissions,
        all_user_permissions=all_user_permissions,
        federation_shares=component_policies,
        get_user=get_user_if_exists,
        get_component=get_component,
        Permissions=Permissions,
        permissions_form=permissions_form,
        users=users,
        groups=groups,
        projects_by_id=all_projects_by_id,
        project_id_hierarchy_list=project_id_hierarchy_list,
        show_projects_form=show_projects_form,
        edit_component_policy_form=edit_component_policy_form,
        component_users=component_users,
        copy_permissions_form=copy_permissions_form,
        add_component_policy_form=add_component_policy_form,
        possible_new_components=possible_new_components,
        add_user_permissions_form=add_user_permissions_form,
        add_group_permissions_form=add_group_permissions_form,
        get_group=get_group,
        add_project_permissions_form=add_project_permissions_form,
        get_project=get_project,
        suggested_user_id=suggested_user_id,
        json_dumps=json.dumps,
        policies=policies
    )


@frontend.route('/objects/<int:object_id>/permissions', methods=['POST'])
@object_permissions_required(Permissions.GRANT)
def update_object_permissions(object_id):
    permissions_form = PermissionsForm()
    add_user_permissions_form = UserPermissionsForm()
    add_group_permissions_form = GroupPermissionsForm()
    add_project_permissions_form = ProjectPermissionsForm()
    add_component_policy_form = ObjectNewShareAccessForm()
    edit_component_policy_form = ObjectEditShareAccessForm()
    copy_permissions_form = CopyPermissionsForm()
    if 'copy_permissions' in flask.request.form:
        if not flask.current_app.config["LOAD_OBJECTS_IN_BACKGROUND"]:
            existing_objects = get_objects_with_permissions(
                user_id=flask_login.current_user.id,
                permissions=Permissions.GRANT
            )
            copy_permissions_form.object_id.choices = [
                (str(existing_object.id), existing_object.name)
                for existing_object in existing_objects
                if existing_object.id != object_id
            ]
        else:
            copy_permissions_form.object_id.choices = []
        if copy_permissions_form.validate_on_submit():
            logic.object_permissions.copy_permissions(object_id, int(copy_permissions_form.object_id.data))
            logic.object_permissions.set_user_object_permissions(object_id, flask_login.current_user.id, Permissions.GRANT)
            flask.flash(_("Successfully copied object permissions."), 'success')
    elif 'add_component_policy' in flask.request.form and add_component_policy_form.validate_on_submit():
        component_id = add_component_policy_form.component_id.data
        component = get_component(component_id)
        policy = {
            'access': {
                'data': add_component_policy_form.data.data,
                'action': add_component_policy_form.action.data,
                'users': add_component_policy_form.users.data,
                'files': add_component_policy_form.files.data,
                'comments': add_component_policy_form.comments.data,
                'object_location_assignments': add_component_policy_form.object_location_assignments.data,
            },
            'permissions': {'users': {}, 'groups': {}, 'projects': {}, 'all_users': 'none'}
        }
        allowed_permissions = [Permissions.READ.name.lower(), Permissions.WRITE.name.lower(), Permissions.GRANT.name.lower()]
        for attr_name, value in flask.request.form.items():
            if attr_name[:len('permissions_add_policy_user_')] == 'permissions_add_policy_user_':
                user_id = attr_name[len('permissions_add_policy_user_'):]
                try:
                    user_id = int(user_id)
                except ValueError:
                    flask.flash(_("A problem occurred while changing the object permissions. Please try again."), 'error')
                if value not in allowed_permissions:
                    flask.flash(_("A problem occurred while changing the object permissions. Please try again."), 'error')
                policy['permissions']['users'][user_id] = value
        add_object_share(object_id, component_id, policy)
        try:
            update_poke_component(component)
        except logic.errors.MissingComponentAddressError:
            flask.flash(_('Unable to contact %(component_name)s. Missing database address.', component_name=component.get_name()), 'warning')
        except logic.errors.NoAuthenticationMethodError:
            flask.flash(_('No valid authentication method configured for %(component_name)s (%(component_address)s).', component_name=component.get_name(), component_address=component.address), 'warning')
        except requests.ConnectionError:
            flask.flash(_('Unable to contact %(component_name)s (%(component_address)s).', component_name=component.get_name(), component_address=component.address), 'warning')
        flask.flash(_("Successfully updated object permissions."), 'success')
    elif 'edit_component_policy' in flask.request.form and edit_component_policy_form.validate_on_submit():
        component_id = edit_component_policy_form.component_id.data
        component = get_component(component_id)
        policy = {
            'access': {
                'data': edit_component_policy_form.data.data,
                'action': edit_component_policy_form.action.data,
                'users': edit_component_policy_form.users.data,
                'files': edit_component_policy_form.files.data,
                'comments': edit_component_policy_form.comments.data,
                'object_location_assignments': edit_component_policy_form.object_location_assignments.data,
            },
            'permissions': {'users': {}, 'groups': {}, 'projects': {}, 'all_users': 'none'}
        }
        allowed_permissions = [Permissions.READ.name.lower(), Permissions.WRITE.name.lower(), Permissions.GRANT.name.lower()]
        for attr_name, value in flask.request.form.items():
            if attr_name[:len('permissions_edit_policy_user_')] == 'permissions_edit_policy_user_':
                user_id = attr_name[len('permissions_edit_policy_user_'):]
                try:
                    user_id = int(user_id)
                except ValueError:
                    flask.flash(_("A problem occurred while changing the object permissions. Please try again."), 'error')
                    return flask.redirect(flask.url_for('.object_permissions', object_id=object_id))
                if value not in allowed_permissions:
                    flask.flash(_("A problem occurred while changing the object permissions. Please try again."), 'error')
                    return flask.redirect(flask.url_for('.object_permissions', object_id=object_id))
                policy['permissions']['users'][user_id] = value
        update_object_share(object_id, component_id, policy)
        try:
            update_poke_component(component)
        except logic.errors.MissingComponentAddressError:
            flask.flash(_('Unable to contact %(component_name)s. Missing database address.', component_name=component.get_name()), 'warning')
        except logic.errors.NoAuthenticationMethodError:
            flask.flash(_('No valid authentication method configured for %(component_name)s (%(component_address)s).', component_name=component.get_name(), component_address=component.address), 'warning')
        except requests.ConnectionError:
            flask.flash(_('Unable to contact %(component_name)s (%(component_address)s).', component_name=component.get_name(), component_address=component.address), 'warning')
        flask.flash(_("Successfully updated object permissions."), 'success')
    else:
        if handle_permission_forms(
            logic.object_permissions.object_permissions,
            object_id,
            add_user_permissions_form,
            add_group_permissions_form,
            add_project_permissions_form,
            permissions_form
        ):
            user_log.edit_object_permissions(user_id=flask_login.current_user.id, object_id=object_id)
            flask.flash(_("Successfully updated object permissions."), 'success')
        else:
            flask.flash(_("A problem occurred while changing the object permissions. Please try again."), 'error')
    return flask.redirect(flask.url_for('.object_permissions', object_id=object_id))
