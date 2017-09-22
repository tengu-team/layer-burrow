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
from subprocess import call
from charmhelpers.core import hookenv, templating, host
from charmhelpers.core.hookenv import status_set, log, open_port, close_port
from charms.reactive import when, when_not, set_state, remove_state, when_any
from charms.layer.go import go_environment


config = hookenv.config()


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


@when('config.changed.port')
def config_changed_port():
    stop_burrow()
    if config.previous('port'):
        close_port('port')
    remove_state('burrow.configured')


@when_any('config.changed.slack-notifier',
          'config.changed.slack-webhook',
          'config.changed.consumer-groups')
def config_changed_slack():
    # Extra conditions
    stop_burrow()
    remove_state('burrow.configured')


@when('burrow.started')
@when_not('kafka.ready')
def wait_for_kafka():
    status_set('waiting', 'Waiting for Kafka to become ready')
    stop_burrow()


def stop_burrow():
    if host.service_running('burrow'):
        call(['systemctl', 'disable', 'burrow'])
        host.service_stop('burrow')
    remove_state('burrow.started')


@when('go.installed', 'kafka.ready')
@when_not('burrow.configured')
def configure(kafka):
    if not config.get('port'):
        status_set('blocked', 'Waiting for port config')
        return
    hookenv.log('Configuring Burrow')
    status_set('maintenance', 'Configuring Burrow')
    templating.render(source='logging-conf.tmpl',
                      target='/home/ubuntu/burrow/config/logging.cfg',
                      context={})
    context = {
        'logdir': '/home/ubuntu/burrow/log',
        'logconfig': '/home/ubuntu/burrow/config/logging.cfg',
        'api_port': config.get('port'),
        'slack': config.get('slack-notifier')
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

    kafka_cluster_name = 'local'
    for conv in kafka.conversations():
        kafka_cluster_name = conv.key.split('.')[-1].split('/')[0]
        break
    context['cluster'] = kafka_cluster_name

    if (config.get('slack-notifier') and
            config.get('slack-webhook').rstrip() and
            config.get('consumer-groups').rstrip() and
            config.get('slack-channel').rstrip()):
        context['slack_webhook'] = config.get('slack-webhook').rstrip()
        context['groups'] = config.get('consumer-groups').rstrip().split(' ')
        context['channel'] = config.get('slack-channel').rstrip()

    templating.render(source='burrow-conf.tmpl',
                      target='/home/ubuntu/burrow/config/burrow.cfg',
                      context=context)

    go_env = go_environment()
    systemd_context = {
        'burrow_path': go_env['GOPATH'] + '/bin/Burrow',
        'config_path': '/home/ubuntu/burrow/config/burrow.cfg',
    }


    templating.render(source='unit_file.tmpl',
                      target='/etc/systemd/system/burrow.service',
                      context=systemd_context)

    open_port(config.get('port'))
    set_state('burrow.configured')


@when('burrow.configured', 'kafka.ready')
@when_not('burrow.started')
def start(kafka):
    hookenv.log('Starting burrow')
    if not host.service_running('burrow'):
        call(['systemctl', 'enable', 'burrow'])
        host.service_start('burrow')
    status_set('active', 'ready (:' + str(config.get('port')) + ')')
    set_state('burrow.started')


@when('http.available')
def configure_http(http):
    http.configure(port=config.get('port'))
