import datetime
import inspect
import os
import subprocess
import sys
import textwrap
import time
import uuid

import varlink


if sys.version_info[0] == 2:
    raise ImportError("The mock module isn't compatible with python 2")


def cast_type(typeof):
    cast = {'str': 'string'}
    typeof = str(typeof).replace("<class '", "").replace("'>", "")
    return cast.get(typeof, typeof)


def get_ignored():
    ignore = dir(MockedService)
    return ignore


def get_interface_attributs(interface, ignored):
    attributs = {"callables": [], "others": []}
    for attr in dir(interface):
        if attr in ignored:
            continue
        attribut = getattr(interface, attr)
        if callable(attribut):
            attributs["callables"].append(attr)
        else:
            attributs["others"].append(attr)
    return attributs


def generate_callable_interface(interface, attr):
    attribut = getattr(interface, attr)
    signature = inspect.signature(attribut)
    params = signature.parameters.values()
    sign = []
    for param in params:
        if param.name == "self":
            continue
        typeof = param.annotation
        sign.append("{}: {}".format(param.name, cast_type(typeof)))
    returned = signature.return_annotation
    if returned:
        returned = cast_type(returned)
        doc = attribut.__doc__
        if not doc:
            raise ValueError(
                "docstring format must be:"
                "return name: type")
        doc = doc.replace("return ", "")
        returned = "{}: {}".format(doc, returned)
    else:
        returned = ""
    return "method {name}({signature}) -> ({returned})".format(
        name=attr,
        signature=",".join(sign),
        returned=returned
    )


class MockedServiceProcess():
    address = None
    vendor = None
    product = None
    version = None
    url = None
    interface = None
    interface_file = None
    interface_name = None
    interface_content = None
    service_to_mock = None

    def run(self):
        mocked_service = varlink.Service(
            vendor=self.vendor,
            product=self.product,
            version=self.version,
            url=self.url)
        instanciated_service = self.service_to_mock()
        mocked_service._set_interface(
            self.interface_file,
            instanciated_service)

        class ServiceRequestHandler(varlink.RequestHandler):
            service = mocked_service

        self.varlink_server = varlink.ThreadingServer(
            self.address, ServiceRequestHandler)
        self.varlink_server.serve_forever()


def service_generator(service, info, filename="mockedservice.py"):
    with open(filename, "w+") as pyfp:
        pyfp.write(textwrap.dedent("""\
        '''
            Generated by varlink mocking system

            {datetime}
            Only for testing purpose and unit testing
        '''
        """.format(datetime=datetime.datetime.now())))
        pyfp.write("import varlink\n\n")
        pyfp.write(inspect.getsource(service))
        pyfp.write("\n\n")
        pyfp.write(inspect.getsource(MockedServiceProcess))
        pyfp.write("\n\n")
        pyfp.write("if __name__ == '__main__':\n")
        pyfp.write("    msp = MockedServiceProcess()\n")
        for key, value in info.items():
            surround = "'"
            if value["type"] == "raw":
                surround = ""
            pyfp.write("    msp.{key} = {surround}{value}{surround}\n".format(
                key=key, value=value["value"], surround=surround))
        pyfp.write("    msp.run()\n")


def mockedservice(fake_service=None, address='unix:@test', name=None,
                  vendor='varlink', product='mock', version=1,
                  url='http://localhost'):
    """
    Varlink mocking service

    To mock a fake service and merely test your varlink client against.

    The mocking feature is for testing purpose, it's allow
    you to test your varlink client against a fake service which will
    returned self handed result defined in your object who will be mocked.

    Example:

    >>> import unittest
    >>> from varlink import mock
    >>> import varlink
    >>>
    >>>
    >>> class Service():
    >>>
    >>>     def Test1(self, param1: int) -> dict:
    >>>         '''
    >>>         return (test: string)
    >>>         '''
    >>>         return {"test": param1}
    >>>
    >>>     def Test2(self, param1: str) -> dict:
    >>>         '''
    >>>         return (test: string)
    >>>         '''
    >>>         return {"test": param1}
    >>>
    >>>     def Test3(self, param1: int) -> dict:
    >>>         '''
    >>>         return (test: int, boom: string, foo: string, bar: 42)
    >>>         '''
    >>>         return {
    >>>             "test": param1 * 2,
    >>>             "boom": "foo",
    >>>             "foo": "bar",
    >>>             "bar": 42,
    >>>         }
    >>>
    >>>
    >>> class TestMyClientWithMockedService(unittest.TestCase):
    >>>
    >>>     @mock.mockedservice(
    >>>         fake_service=Service,
    >>>         name='org.service.com',
    >>>         address='unix:@foo'
    >>>     )
    >>>     def test_my_client_against_a_mock(self):
    >>>         with varlink.Client("unix:@foo") as client:
    >>>             connection = client.open('org.service.com')
    >>>             self.assertEqual(
    >>>                 connection.Test1(param1=1)["test"], 1)
    >>>             self.assertEqual(
    >>>                    connection.Test2(param1="foo")["test"], "foo")
    >>>             self.assertEqual(
    >>>                    connection.Test3(param1=6)["test"], 12)
    >>>             self.assertEqual(
    >>>                    connection.Test3(param1=6)["bar"], 42)

    First you need to define a sample class that will be passed to your
    decorator `mock.mockedservice` and then a service will be initialized
    and launched automatically, and after that you just need to connect your
    client to him and to establish your connection then now you can
    call your methods and it will give you the expected result.

    The mocking module is only compatible with python 3 or higher version
    of python because this module require annotation to generate interface
    description.

    If you try to use it with python 2.x it will raise an ``ImportError``.
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            with MockedService(fake_service, name=name,
                               address=address):
                try:
                    func(*args, **kwargs)
                except BrokenPipeError:
                    # manage fake service stoping
                    pass
            return
        return wrapper
    return decorator


class MockedService():

    def __init__(self, service, address='unix:@test', name=None,
                 vendor='varlink', product='mock', version=1,
                 url='http://localhost'):
        if not name:
            module = service.__module__
            try:
                self.name = os.path.splitext(module)[1].replace('.', '')
            except IndexError:
                self.name = module
        else:
            self.name = name
        self.identifier = str(uuid.uuid4())
        self.interface_description = None
        self.service = service
        self.address = address
        self.vendor = vendor
        self.product = product
        self.version = version
        self.url = url
        self.service_info = {
            "address": {'type': 'inherited', 'value': address},
            "vendor": {'type': 'inherited', 'value': vendor},
            "product": {'type': 'inherited', 'value': product},
            "version": {'type': 'raw', 'value': version},
            "url": {'type': 'inherited', 'value': url},
            "interface_name": {'type': 'inherited', 'value': self.name},
            "interface_file": {
                'type': 'inherited',
                'value': self.get_interface_file_path()},
            "service_to_mock": {'type': 'raw', 'value': service.__name__}
        }
        self.generate_interface()

    def generate_interface(self):
        ignore = get_ignored()
        self.interface_description = ["interface {}".format(self.name)]
        attributs = get_interface_attributs(self.service, ignore)
        for attr in attributs["callables"]:
            self.interface_description.append(generate_callable_interface(
                self.service, attr))

    def get_interface_file_path(self):
        return "/tmp/{}".format(self.name)

    def generate_interface_file(self):
        tfp = open(self.get_interface_file_path(), "w+")
        tfp.write("\n".join(self.interface_description))
        tfp.close()

    def delete_interface_files(self):
        os.remove(self.get_interface_file_path())
        os.remove(self.mocked_service_file)

    def service_start(self):
        self.service_pid = subprocess.Popen(
            [sys.executable, self.mocked_service_file]
        )
        time.sleep(2)

    def service_stop(self):
        self.service_pid.kill()
        self.service_pid.communicate()

    def __enter__(self):
        self.mocked_service_file = "/tmp/{}".format(self.identifier)
        service_generator(
            self.service, self.service_info,
            filename=self.mocked_service_file)
        self.generate_interface_file()
        self.service_start()
        return self

    def __exit__(self, type, value, traceback):
        self.service_stop()
        self.delete_interface_files()
