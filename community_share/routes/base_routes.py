import logging

from flask import jsonify, request, Blueprint

from sqlalchemy import Boolean
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.orm.attributes import instance_state

from community_share import store
from community_share.utils import StatusCodes, is_integer
from community_share.authorization import get_requesting_user
from community_share.models.base import ValidationException

logger = logging.getLogger(__name__)

API_MANY_FORMAT = '/api/{0}'
API_SINGLE_FORMAT = '/api/{0}/<id>'

def make_not_authorized_response():
    response_data = {'message': 'Authorization failed'}
    response = jsonify(response_data)
    response.status_code = StatusCodes.NOT_AUTHORIZED
    return response

def make_forbidden_response():
    response_data = {'message': 'Forbidden'}
    response = jsonify(response_data)
    response.status_code = StatusCodes.FORBIDDEN
    return response

def make_not_found_response():
    response_data = {'message': 'Not found'}
    response = jsonify(response_data)
    response.status_code = StatusCodes.NOT_FOUND
    return response

def make_bad_request_response(message=None):
    if message is None:
        message = 'Bad request'
    response_data = {'message': message}
    response = jsonify(response_data)
    response.status_code = StatusCodes.BAD_REQUEST
    return response

def make_OK_response(message=None):
    if message is None:
        message = 'OK'
    response_data = {'message': message}
    response = jsonify(response_data)
    response.status_code = StatusCodes.OK
    return response

def make_server_error_response(message=None):
    if message is None:
        message = 'Server error'
    response_data = {'message': message}
    response = jsonify(response_data)
    response.status_code = StatusCodes.SERVER_ERROR
    return response

def make_standard_many_response(items):
    serialized = [item.standard_serialize() for item in items]
    response_data = {'data': serialized}
    response = jsonify(response_data)
    return response

def make_admin_many_response(items):
    serialized = [item.admin_serialize() for item in items]
    response_data = {'data': serialized}
    response = jsonify(response_data)
    return response

def make_mixed_many_response(items, requester):
    serialized = []
    for item in items:
        if item.has_admin_rights(requester):
            serialized.append(item.admin_serialize())
        elif item.has_standard_rights(requester):
            serialized.append(item.standard_serialize())
    response_data = {'data': serialized}
    response = jsonify(response_data)
    return response

def make_standard_single_response(item):
    if item is None:
        response = make_not_found_response()
    else:
        serialized = item.standard_serialize()
        response_data = {'data': serialized}
        response = jsonify(response_data)
    return response

def make_admin_single_response(item, include_user=None):
    '''
    Sometimes we want to include the current user info in the response
    since it might be changed by a request.
    '''
    if item is None:
        response = make_not_found_response()
    else:
        serialized = item.admin_serialize()
        response_data = {'data': serialized}
        if include_user is not None:
            serialized_user = include_user.admin_serialize()
            response_data['user'] = serialized_user
        response = jsonify(response_data)
    return response


def make_blueprint(Item, resourceName):

    api = Blueprint(resourceName, __name__)

    @api.route(API_MANY_FORMAT.format(resourceName), methods=['GET'])
    def get_items():
        logger.debug('get_items - {0}'.format(resourceName) )
        requester = get_requesting_user()
        if requester is None and not Item.PERMISSIONS.get('all_can_read_many', False):
            response = make_not_authorized_response()
        else:
            if requester is None or not requester.is_administrator:
                if (Item.PERMISSIONS.get('standard_can_read_many', False) or 
                    Item.PERMISSIONS.get('all_can_read_many', False)):
                    try:
                        query = Item.args_to_query(request.args, requester)
                        items = query.all()
                        response = make_mixed_many_response(items, requester)
                    except ValueError as e:
                        error_message = ', '.join(e.args)
                        response = make_bad_request_response(e.args[0])
                else:
                    response = make_forbidden_response()
            else:
                try:
                    query = Item.args_to_query(request.args, requester)
                    items = query.all()
                    response = make_admin_many_response(items)
                except ValueError as e:
                    error_message = ', '.join(e.args)
                    response = make_bad_request_response(e.args[0])
        return response

    @api.route(API_SINGLE_FORMAT.format(resourceName), methods=['GET'])
    def get_item(id):
        requester = get_requesting_user()
        if requester is None:
            response = make_not_authorized_response()
        elif not is_integer(id):
            response = make_bad_request_user()
        else:
            item = store.session.query(Item).filter_by(id=id).first()
            if item is None:
                response = make_not_found_response()
            else:
                if item.has_admin_rights(requester):
                    response = make_admin_single_response(item)
                elif item.has_standard_rights(requester):
                    response = make_standard_single_response(item)
                else:
                    response = make_forbidden_response()
        return response

    @api.route(API_MANY_FORMAT.format(resourceName), methods=['POST'])
    def add_item():
        requester = get_requesting_user()
        logger.debug('add_item: requester = {0}'.format(requester))
        data = request.json
        if not Item.has_add_rights(data, requester):
            if requester is None:
                logger.debug('not authorized')
                response = make_not_authorized_response()
            else:
                logger.debug('forbidden')
                response = make_forbidden_response()
        else:
            data = request.json
            logger.debug('data send is {0}'.format(data))
            try:
                item = Item.admin_deserialize_add(data)
                store.session.add(item)
                item.on_edit(requester, unchanged=False)
                store.session.commit()
                refreshed_item = store.session.query(Item).filter_by(id=item.id).first()
                if refreshed_item.has_admin_rights(requester):
                    response = make_admin_single_response(
                        refreshed_item, include_user=requester)
                else:
                    response = make_standard_single_response(refreshed_item)
            except ValidationException as e:
                response = make_bad_request_response(str(e))
            except (IntegrityError, InvalidRequestError) as e:
                response = make_server_error_response()
        return response
        
    @api.route(API_SINGLE_FORMAT.format(resourceName), methods=['PATCH', 'PUT'])
    def edit_item(id):
        requester = get_requesting_user()
        if requester is None:
            response = make_not_authorized_response()
        elif not is_integer(id):
            response = make_bad_request_response()
        else:
            id = int(id)
            data = request.json
            data_id = data.get('id', None)
            if data_id is not None and int(data_id) != id:
                response = make_bad_request_response()
            else:
                if id is None:
                    item = None
                else:
                    item = store.session.query(Item).filter_by(id=id).first()
                if item is None:
                    response = make_not_found_response()
                else:
                    if item.has_admin_rights(requester):
                        try:
                            item.admin_deserialize_update(data)
                            store.session.add(item)
                            logger.debug('calling on_edit on {0}'.format(item))
                            item.on_edit(requester, unchanged = not store.session.dirty)
                            store.session.commit()
                            response = make_admin_single_response(item)
                        except ValidationException as e:
                            response = make_bad_request_response(str(e))
                    else:
                        response = make_forbidden_response()
        return response

    @api.route(API_SINGLE_FORMAT.format(resourceName), methods=['DELETE'])
    def delete_item(id):
        requester = get_requesting_user()
        if requester is None:
            response = make_not_authorized_response()
        elif not is_integer(id):
            response = make_bad_request_response()
        else:
            id = int(id)
            item = store.session.query(Item).filter_by(id=id).first()
            if item is None:
                response = make_not_found_response()
            else:
                if item.has_admin_rights(requester):
                    previously_deleted = not item.active
                    item.active = False
                    store.session.add(item)
                    if not previously_deleted:
                        item.on_edit(requester, unchanged=False)
                    store.session.commit()
                    response = make_admin_single_response(item)
                else:
                    response = make_forbidden_response()
        return response
        
    return api

