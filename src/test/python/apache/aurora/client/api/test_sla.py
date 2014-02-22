#
# Copyright 2014 Apache Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import unittest
import time

from apache.aurora.client.api.sla import DomainUpTimeSlaVector, JobUpTimeSlaVector, Sla
from apache.aurora.common.aurora_job_key import AuroraJobKey

from gen.apache.aurora.AuroraSchedulerManager import Client as scheduler_client
from gen.apache.aurora.constants import ACTIVE_STATES
from gen.apache.aurora.ttypes import (
    AssignedTask,
    Identity,
    Quota,
    Response,
    ResponseCode,
    Result,
    ScheduleStatus,
    ScheduleStatusResult,
    ScheduledTask,
    TaskConfig,
    TaskEvent,
    TaskQuery
)

from mock import Mock


class SlaTest(unittest.TestCase):
  def setUp(self):
    self._scheduler = Mock()
    self._sla = Sla(self._scheduler)
    self._cluster = 'cl'
    self._role = 'mesos'
    self._name = 'job'
    self._env = 'test'
    self._job_key = AuroraJobKey(self._cluster, self._role, self._env, self._name)

  def mock_get_tasks(self, tasks, response_code=None):
    response_code = ResponseCode.OK if response_code is None else response_code
    resp = Response(responseCode=response_code, message='test')
    resp.result = Result(scheduleStatusResult=ScheduleStatusResult(tasks=tasks))
    self._scheduler.getTasksStatus.return_value = resp

  def create_task(self, duration, id, host=None, name=None):
    return ScheduledTask(
        assignedTask=AssignedTask(
            instanceId=id,
            slaveHost=host,
            task=TaskConfig(
                production=True,
                jobName=name or self._name,
                owner=Identity(role=self._role),
                environment=self._env)),
        status=ScheduleStatus.RUNNING,
        taskEvents=[TaskEvent(
            status=ScheduleStatus.STARTING,
            timestamp=(time.time() - duration) * 1000)]
    )

  def create_tasks(self, durations):
    return [self.create_task(duration, index) for index, duration in enumerate(durations)]

  def assert_count_result(self, percentage, duration):
    vector = self._sla.get_job_uptime_vector(self._job_key)
    actual = vector.get_task_up_count(duration)
    assert percentage == actual, (
        'Expected percentage:%s Actual percentage:%s' % (percentage, actual)
    )
    self.expect_task_status_call()

  def assert_uptime_result(self, expected, percentile):
    vector = self._sla.get_job_uptime_vector(self._job_key)
    try:
      actual = vector.get_job_uptime(percentile)
    except ValueError:
      assert expected is None, 'Unexpected error raised.'
    else:
      assert expected is not None, 'Expected error not raised.'
      assert expected == actual, (
          'Expected uptime:%s Actual uptime:%s' % (expected, actual)
      )
      self.expect_task_status_call()

  def assert_safe_domain_result(self, host, percentage, duration, in_limit=None, out_limit=None):
    vector = self._sla.get_domain_uptime_vector(self._cluster)
    result = vector.get_safe_hosts(percentage, duration, in_limit)
    assert 1 == len(result), ('Expected length:%s Actual length:%s' % (1, len(result)))
    assert host in result, ('Expected host:%s not found in result' % host)
    if out_limit:
      assert result[host][0].job.name == out_limit.job.name, (
          'Expected job:%s Actual:%s' % (out_limit.job.name, result[host][0].job.name)
      )
      assert result[host][0].percentage == out_limit.percentage, (
        'Expected %%:%s Actual %%:%s' % (out_limit.percentage, result[host][0].percentage)
      )
      assert result[host][0].duration == out_limit.duration, (
        'Expected duration:%s Actual duration:%s' % (out_limit.duration, result[host][0].duration)
      )
    self._scheduler.getTasksStatus.assert_called_once_with(TaskQuery(statuses=ACTIVE_STATES))

  def expect_task_status_call(self):
    self._scheduler.getTasksStatus.assert_called_once_with(
        TaskQuery(
            owner=Identity(role=self._role),
            environment=self._env,
            jobName=self._name,
            statuses=ACTIVE_STATES)
    )


  def test_count_0(self):
    self.mock_get_tasks([])
    self.assert_count_result(0, 0)

  def test_count_50(self):
    self.mock_get_tasks(self.create_tasks([600, 900, 100, 200]))
    self.assert_count_result(50, 300)

  def test_count_100(self):
    self.mock_get_tasks(self.create_tasks([100, 200, 300, 400, 500]))
    self.assert_count_result(100, 50)

  def test_uptime_empty(self):
    self.mock_get_tasks([])
    self.assert_uptime_result(0, 50)

  def test_uptime_0(self):
    self.mock_get_tasks(self.create_tasks([100, 200, 300, 400]))
    self.assert_uptime_result(None, 0)

  def test_uptime_10(self):
    self.mock_get_tasks(self.create_tasks([100, 200, 300, 400]))
    self.assert_uptime_result(400, 10)

  def test_uptime_50(self):
    self.mock_get_tasks(self.create_tasks([100, 200, 300, 400]))
    self.assert_uptime_result(200, 50)

  def test_uptime_99(self):
    self.mock_get_tasks(self.create_tasks([100, 200, 300, 400]))
    self.assert_uptime_result(100, 99)

  def test_uptime_100(self):
    self.mock_get_tasks(self.create_tasks([100, 200, 300, 400]))
    self.assert_uptime_result(None, 100)

  def test_domain_uptime_no_tasks(self):
    self.mock_get_tasks([])
    vector = self._sla.get_domain_uptime_vector(self._cluster)
    assert 0 == len(vector.get_safe_hosts(50, 400)), 'Length must be empty.'

  def test_domain_uptime_no_result(self):
    self.mock_get_tasks([
        self.create_task(100, 1, 'h1', 'j1'),
        self.create_task(200, 2, 'h2', 'j1')
    ])
    vector = self._sla.get_domain_uptime_vector(self._cluster)
    assert 0 == len(vector.get_safe_hosts(50, 400)), 'Length must be empty.'

  def test_domain_uptime(self):
    self.mock_get_tasks([
      self.create_task(100, 1, 'h1', 'j1'),
      self.create_task(200, 2, 'h2', 'j1'),
      self.create_task(100, 1, 'h2', 'j2')
    ])
    self.assert_safe_domain_result('h1', 50, 200)

  def test_domain_uptime_with_override(self):
    self.mock_get_tasks([
      self.create_task(100, 1, 'h1', self._name),
      self.create_task(200, 2, 'h2', self._name),
      self.create_task(100, 1, 'h2', 'j2')
    ])

    job_override = {
        self._job_key:
        DomainUpTimeSlaVector.JobUpTimeLimit(
            job=self._job_key,
            percentage=50,
            duration_seconds=100)
    }
    self.assert_safe_domain_result('h1', 50, 400, in_limit=job_override)
