# coding: utf-8
"""
Authentication functions for the SampleDB federation RESTful API.
"""

import flask

from flask_httpauth import HTTPTokenAuth

from ...logic.component_authentication import login_via_component_token

http_token_auth = HTTPTokenAuth(scheme='Bearer')


@http_token_auth.verify_token
def verify_token(component_token):
    if not component_token:
        return None
    component = login_via_component_token(component_token)
    if component is None:
        return None
    flask.g.component = component
    return component
