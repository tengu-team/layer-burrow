#!/usr/bin/env python3
# Copyright (C) 2017  Ghent University
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import os
from subprocess import call
from jujubigdata import utils
from charmhelpers.core import hookenv, templating, host
from charmhelpers.core.hookenv import status_set, log, open_port
from charms.reactive import when, when_not, set_state, remove_state
from charms.layer.go import go_environment


@when('go.installed')
@when_not('burrow.installed')
def install_burrow():
    previous_wd = os.getcwd()
    go_env = go_environment()
    utils.run_as('ubuntu', 'go', 'get', 'github.com/linkedin/Burrow')
    os.chdir(go_env['GOPATH'] + '/src/github.com/linkedin/Burrow')
    # Temporary fix for https://github.com/linkedin/Burrow/issues/222
    with open(go_env['GOPATH'] + '/src/github.com/linkedin/Burrow/Godeps', 'a') as f:
        f.write('github.com/klauspost/crc32          1bab8b35b6bb565f92cbc97939610af9369f942a')
    utils.run_as('ubuntu', 'gpm', 'install')
    utils.run_as('ubuntu', 'go', 'install')
    dirs = ['/home/ubuntu/burrow', '/home/ubuntu/burrow/log', '/home/ubuntu/burrow/config']
    for dir in dirs:
        if not os.path.exists(dir):
            os.makedirs(dir)
    os.chdir(previous_wd)
    set_state('burrow.installed')


@when_not('kafka.ready')
def status_kafka():
    status_set('blocked', 'Waiting for Kafka relation')


@when('kafka.joined', 'go.installed')
@when_not('kafka.ready')
def wait_for_kafka(kafka):
    status_set('waiting', 'Waiting for Kafka to become ready')
    if host.service_running('burrow'):
        host.service_stop('burrow')
    remove_state('burrow.started')


@when('go.installed', 'kafka.ready')
@when_not('burrow.configured')
def configure(kafka):
    hookenv.log('Configuring Burrow')
    templating.render(source='logging-conf.tmpl',
                      target='/home/ubuntu/burrow/config/logging.cfg',
                      context={})
    context = {
        'logdir': '/home/ubuntu/burrow/log',
        'logconfig': '/home/ubuntu/burrow/config/logging.cfg',
        'api_port': 8000,
        'cluster': 'local'
    }

    zookeeper_nodes = []
    zookeeper_port = 2181
    for zookeeper_unit in kafka.zookeepers():
        zookeeper_nodes.append(zookeeper_unit['host'])
        zookeeper_port = zookeeper_unit['port']

    kafka_nodes = []
    kafka_port = 9092
    for kafka_unit in kafka.kafkas():
        kafka_nodes.append(kafka_unit['host'])
        kafka_port = kafka_unit['port']

    context['zoo_units'] = zookeeper_nodes
    context['zoo_port'] = zookeeper_port
    context['kafka_units'] = kafka_nodes
    context['kafka_port'] = kafka_port

    templating.render(source='burrow-conf.tmpl',
                      target='/home/ubuntu/burrow/config/burrow.cfg',
                      context=context)

    go_env = go_environment()
    templating.render(source='unit_file.tmpl',
                      target='/etc/systemd/system/burrow.service',
                      context={
                          'burrow_path': go_env['GOPATH'] + '/bin/Burrow',
                          'config_path': '/home/ubuntu/burrow/config/burrow.cfg'
                      })
    open_port(8000)
    set_state('burrow.configured')


@when('burrow.configured', 'kafka.ready')
@when_not('burrow.started')
def start(kafka):
    hookenv.log('Starting burrow')
    if not host.service_running('burrow'):
        host.service_start('burrow')
    status_set('active', 'ready (:8000)')
    set_state('burrow.started')


@when('http.available')
def configure_http(http):
    http.configure(port=8000)
