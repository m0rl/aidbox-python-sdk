import logging
from fhirpy import FHIRClient
from fhirpy.exceptions import FHIRResourceNotFound
from aiohttp import BasicAuth
from .db import DBProxy

logger = logging.getLogger('aidbox_sdk')


class SDK(object):

    def __init__(self, settings, *, entities=None, resources=None, seeds=None,
                 on_ready=None):
        self._settings = settings
        self._subscriptions = {}
        self._subscription_handlers = {}
        self._operations = {}
        self._operation_handlers = {}
        self._manifest = {
            'id': settings.APP_ID,
            'resourceType': 'App',
            'type': 'app',
            'apiVersion': 1,
            'endpoint': {
                'url': settings.APP_URL,
                'type': 'http-rpc',
                'secret': settings.APP_SECRET,
            }
        }
        self._resources = resources or {}
        self._entities = entities or {}
        self._seeds = seeds or {}
        self._on_ready = on_ready
        self._app_endpoint_name = '{}-endpoint'.format(settings.APP_ID)
        self.client = None
        self.db = DBProxy(self._settings)

    def init_client(self, config):
        basic_auth = BasicAuth(
            login=config['client']['id'],
            password=config['client']['secret'])
        self.client = FHIRClient('{}/fhir'.format(config['box']['base-url']),
                                 authorization=basic_auth.encode(),
                                 without_cache=True)
        self._create_seed_resources()
        if callable(self._on_ready):
            self._on_ready()

    def _create_seed_resources(self):
        for entity, resources in self._seeds.items():
            for resource_id, resource in resources.items():
                try:
                    self.client.resources(entity).get(id=resource_id)
                except FHIRResourceNotFound:
                    seed_resource = self.client.resource(
                        entity,
                        id=resource_id,
                        **resource
                    )
                    seed_resource.save()
                    logger.debug('Created resource "%s" with id "%s"', entity, resource_id)
                else:
                    logger.debug('Resource "%s" with id "%s" already exists', entity, resource_id)

    def build_manifest(self):
        if self._resources:
            self._manifest['resources'] = self._resources
        if self._entities:
            self._manifest['entities'] = self._entities
        if self._subscriptions:
            self._manifest['subscriptions'] = self._subscriptions
        if self._operations:
            self._manifest['operations'] = self._operations
        return self._manifest

    def subscription(self, entity):
        def wrap(func):
            path = func.__name__
            self._subscriptions[entity] = {'handler': path}
            self._subscription_handlers[path] = func
            return func
        return wrap

    def get_subscription_handler(self, path):
        return self._subscription_handlers.get(path)

    def operation(self, methods, path, public=False):
        def wrap(func):
            if not isinstance(path, list):
                raise ValueError('`path` must be a list')
            if not isinstance(methods, list):
                raise ValueError('`methods` must be a list')
            _str_path = []
            for p in path:
                if isinstance(p, str):
                    _str_path.append(p)
                elif isinstance(p, dict):
                    _str_path.append('{{{}}}'.format(p['name']))
            for method in methods:
                operation_id = '{}.{}.{}.{}'.format(method,
                                                    func.__module__,
                                                    func.__name__,
                                                    '_'.join(_str_path))
                self._operations[operation_id] = {
                    'method': method,
                    'path': path,
                }
                self._operation_handlers[operation_id] = func
                if public is True:
                    self._set_access_policy_for_public_op(operation_id)
            return func
        return wrap

    def get_operation_handler(self, operation_id):
        return self._operation_handlers.get(operation_id)

    def _set_access_policy_for_public_op(self, operation_id):
        if 'AccessPolicy' not in self._resources:
            self._resources['AccessPolicy'] = {}
        if self._app_endpoint_name not in self._resources['AccessPolicy']:
            self._resources['AccessPolicy'][self._app_endpoint_name] = {
                'link': [],
                'engine': 'allow',
            }
        self._resources['AccessPolicy'][self._app_endpoint_name]['link'].append({
            'id': operation_id,
            'resourceType': 'Operation',
        })
