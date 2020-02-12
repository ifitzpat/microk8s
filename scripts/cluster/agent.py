#!flask/bin/python
import getopt
import json
import os
import random
import shutil
import socket
import subprocess
import sys

from .common.utils import try_set_file_permissions, get_callback_token

from flask import Flask, jsonify, request, abort, Response

app = Flask(__name__)
CLUSTER_API="cluster/api/v1.0"
snapdata_path = os.environ.get('SNAP_DATA')
snap_path = os.environ.get('SNAP')
cluster_tokens_file = "{}/credentials/cluster-tokens.txt".format(snapdata_path)
callback_token_file = "{}/credentials/callback-token.txt".format(snapdata_path)
default_port = 25000
default_listen_interface = "0.0.0.0"


def get_service_name(service):
    """
    Returns the service name from its configuration file name.

    :param service: the name of the service configuration file
    :returns: the service name
    """
    if service in ["kube-proxy", "kube-apiserver", "kube-scheduler", "kube-controller-manager"]:
        return service[len("kube-"), :]
    else:
        return service


def update_service_argument(service, key, val):
    """
    Adds an argument to the arguments file of the service.

    :param service: the service
    :param key: the argument to add
    :param val: the value for the argument
    """

    args_file = "{}/args/{}".format(snapdata_path, service)
    args_file_tmp = "{}/args/{}.tmp".format(snapdata_path, service)
    found = False
    with open(args_file_tmp, "w+") as bfp:
        with open(args_file, "r+") as fp:
            for _, line in enumerate(fp):
                if line.startswith(key):
                    if val is not None:
                        bfp.write("{}={}\n".format(key, val))
                    found = True
                else:
                    bfp.write("{}\n".format(line.rstrip()))
        if not found and val is not None:
            bfp.write("{}={}\n".format(key, val))

    try_set_file_permissions(args_file_tmp)
    shutil.move(args_file_tmp, args_file)


def remove_token_from_file(token, file):
    """
    Remove a token from the valid tokens set

    :param token: the token to be removed
    :param file: the file to be removed from
    """
    backup_file = "{}.backup".format(file)
    # That is a critical section. We need to protect it.
    # We are safe for now because flask serves one request at a time.
    with open(backup_file, 'w') as back_fp:
        with open(file, 'r') as fp:
            for _, line in enumerate(fp):
                if line.strip() == token:
                    continue
                back_fp.write("{}".format(line))

    shutil.copyfile(backup_file, file)


def get_cert(certificate):
    """
    Return the data of the certificate

    :returns: the certificate file contents
    """
    cert_file = "{}/certs/{}".format(snapdata_path, certificate)
    with open(cert_file) as fp:
        cert = fp.read()
    return cert


def get_cluster_certs():
    """
    Return the cluster certificates

    :returns: the cluster certificate files
    """
    file = "{}/var/kubernetes/backend/cluster.crt".format(snapdata_path)
    with open(file) as fp:
        cluster_cert = fp.read()
    file = "{}/var/kubernetes/backend/cluster.key".format(snapdata_path)
    with open(file) as fp:
        cluster_key = fp.read()

    return cluster_cert, cluster_key


def get_arg(key, file):
    """
    Get an argument from an arguments file

    :param key: the argument we look for
    :param file: the arguments file to search in
    :returns: the value of the argument or None(if the key doesn't exist)
    """
    filename = "{}/args/{}".format(snapdata_path, file)
    with open(filename) as fp:
        for _, line in enumerate(fp):
            if line.startswith(key):
                args = line.split(' ')
                args = args[-1].split('=')
                return args[-1].rstrip()
    return None


def is_valid(token_line, token_type=cluster_tokens_file):
    """
    Check whether a token is valid

    :param token: token to be checked
    :param token_type: the type of token (bootstrap or signature)
    :returns: True for a valid token, False otherwise
    """
    token = token_line.strip()
    # Ensure token is not empty
    if not token:
        return False

    with open(token_type) as fp:
        for _, line in enumerate(fp):
            if token == line.strip():
                return True
    return False


def read_kubelet_args_file(node=None):
    """
    Return the contents of the kubelet arguments file
    
    :param node: node to add a host override (defaults to None)
    :returns: the kubelet args file
    """
    filename = "{}/args/kubelet".format(snapdata_path)
    with open(filename) as fp:
        args = fp.read()
        if node:
            args = "{}--hostname-override {}".format(args, node)
        return args


def get_node_ep(hostname, remote_addr):
    """
    Return the endpoint to be used for the node based by trying to resolve the hostname provided
    
    :param hostname: the provided hostname
    :param remote_addr: the address the request came from
    :returns: the node's location
    """
    try:
        socket.gethostbyname(hostname)
        return hostname
    except socket.gaierror:
        return remote_addr
    return remote_addr


@app.route('/{}/join'.format(CLUSTER_API), methods=['POST'])
def join_node():
    """
    Web call to join a node to the cluster
    """
    if request.headers['Content-Type'] == 'application/json':
        token = request.json['token']
        hostname = request.json['hostname']
        port = request.json['port']
    else:
        token = request.form['token']
        hostname = request.form['hostname']
        port = request.form['port']

    if not is_valid(token):
        error_msg={"error": "Invalid token"}
        return Response(json.dumps(error_msg), mimetype='application/json', status=500)

    callback_token = get_callback_token()
    remove_token_from_file(token, cluster_tokens_file)
    node_addr = get_node_ep(hostname, request.remote_addr)

    api_port = get_arg('--secure-port', 'kube-apiserver')
    subprocess.check_call("systemctl restart snap.microk8s.daemon-apiserver.service".split())
    kubelet_args = read_kubelet_args_file()
    cluster_cert, cluster_key = get_cluster_certs()

    return jsonify(ca=get_cert("ca.crt"),
                   ca_key=get_cert("ca.key"),
                   server_cert=get_cert("server.crt"),
                   server_cert_key=get_cert("server.key"),
                   service_account_key=get_cert("serviceaccount.key"),
                   proxy_cert=get_cert("front-proxy-client.crt"),
                   proxy_cert_key=get_cert("front-proxy-client.key"),
                   cluster_cert=cluster_cert,
                   cluster_key=cluster_key,
                   cluster_port='19001',
                   callback_token=callback_token,
                   apiport=api_port,
                   kubelet_args=kubelet_args,
                   hostname_override=node_addr)


    token = token.strip()
@app.route('/{}/configure'.format(CLUSTER_API), methods=['POST'])
def configure():
    """
    Web call to configure the node
    """
    if request.headers['Content-Type'] == 'application/json':
        callback_token = request.json['callback']
        configuration = request.json
    else:
        callback_token = request.form['callback']
        configuration = json.loads(request.form['configuration'])

    callback_token = callback_token.strip()
    if not is_valid(callback_token, callback_token_file):
        error_msg={"error": "Invalid token"}
        return Response(json.dumps(error_msg), mimetype='application/json', status=500)

    # We expect something like this:
    '''
    {
      "callback": "xyztoken"
      "service":
      [
        {
          "name": "kubelet",
          "arguments_remove":
          [
            "myoldarg"
          ],
          "arguments_update":
          [
            {"myarg": "myvalue"},
            {"myarg2": "myvalue2"},
            {"myarg3": "myvalue3"}
          ],
          "restart": False
        },
        {
          "name": "kube-proxy",
          "restart": True
        }
      ],
      "addon":
      [
        {
          "name": "gpu",
          "enable": True
        },
        {
          "name": "gpu",
          "disable": True
        }
      ]
    }
    '''

    if "service" in configuration:
        for service in configuration["service"]:
            print("{}".format(service["name"]))
            if "arguments_update" in service:
                print("Updating arguments")
                for argument in service["arguments_update"]:
                    for key, val in argument.items():
                        print("{} is {}".format(key, val))
                        update_service_argument(service["name"], key, val)
            if "arguments_remove" in service:
                print("Removing arguments")
                for argument in service["arguments_remove"]:
                    print("{}".format(argument))
                    update_service_argument(service["name"], argument, None)
            if "restart" in service and service["restart"]:
                service_name = get_service_name(service["name"])
                print("restarting {}".format(service["name"]))
                subprocess.check_call("systemctl restart snap.microk8s.daemon-{}.service".format(service_name).split())

    if "addon" in configuration:
        for addon in configuration["addon"]:
            print("{}".format(addon["name"]))
            if "enable" in addon and addon["enable"]:
                print("Enabling {}".format(addon["name"]))
                subprocess.check_call("{}/microk8s-enable.wrapper {}".format(snap_path, addon["name"]).split())
            if "disable" in addon and addon["disable"]:
                print("Disabling {}".format(addon["name"]))
                subprocess.check_call("{}/microk8s-disable.wrapper {}".format(snap_path, addon["name"]).split())

    resp_date = {"result": "ok"}
    resp = Response(json.dumps(resp_date), status=200, mimetype='application/json')
    return resp


def usage():
    print("Agent responsible for setting up a cluster. Arguments:")
    print("-l, --listen:   interfaces to listen to (defaults to {})".format(default_listen_interface))
    print("-p, --port:     port to listen to (default {})".format(default_port))


if __name__ == '__main__':
    server_cert = "{SNAP_DATA}/certs/server.crt".format(SNAP_DATA=snapdata_path)
    server_key = "{SNAP_DATA}/certs/server.key".format(SNAP_DATA=snapdata_path)
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], "hl:p:", ["help", "listen=", "port="])
    except getopt.GetoptError as err:
        print(err)  # will print something like "option -a not recognized"
        usage()
        sys.exit(2)
    port = default_port
    listen = default_listen_interface
    for o, a in opts:
        if o in ("-l", "--listen"):
            listen = a
        if o in ("-p", "--port"):
            port = a
        elif o in ("-h", "--help"):
            usage()
            sys.exit(1)
        else:
            assert False, "unhandled option"

    app.run(host=listen, port=port, ssl_context=(server_cert, server_key))
