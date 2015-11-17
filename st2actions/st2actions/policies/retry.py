# Licensed to the StackStorm, Inc ('StackStorm') under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import copy

from st2common import log as logging
from st2common.models.db.liveaction import LiveActionDB
import st2common.services.action as action_services
from st2common.constants.action import LIVEACTION_STATUS_FAILED
from st2common.constants.action import LIVEACTION_STATUS_TIMED_OUT
from st2common.util.enum import Enum
from st2common.policies.base import ResourcePolicyApplicator

__all__ = [
    'RetryOnPolicy',
    'ExecutionRetryPolicyApplicator'
]

# In context of new execadd original execution id, policy ref, etc
# retry means re-running an action new execution with same parameters

LOG = logging.getLogger(__name__)

VALID_RETRY_STATUSES = [
    LIVEACTION_STATUS_FAILED,
    LIVEACTION_STATUS_TIMED_OUT
]

# Maximum value for "max_retry_count" parameter
MAXIMUM_RETRY_COUNT = 5


class RetryOnPolicy(Enum):
    FAILURE = 'failure'  # Retry on execution failure
    TIMEOUT = 'timeout'  # Retry on execution timeout


class ExecutionRetryPolicyApplicator(ResourcePolicyApplicator):
    # TODO: Retry or ReRun?
    def __init__(self, policy_ref, policy_type, retry_on, max_retry_count=2):
        """
        :param retry_on: Condition to retry the execution on.
        :type retry_on: ``str``

        :param max_retry_count: Maximum number of times to try to retry an action.
        :type max_retry_count: ``int``
        """
        super(ExecutionRetryPolicyApplicator, self).__init__(policy_ref=policy_ref,
                                                             policy_type=policy_type)

        if retry_on not in RetryOnPolicy.get_valid_values():
            raise ValueError('Invalid value for "retry_on" parameter')

        if max_retry_count > MAXIMUM_RETRY_COUNT:
            raise ValueError('Maximum retry count is %s' % (MAXIMUM_RETRY_COUNT))

        self.retry_on = retry_on
        self.max_retry_count = max_retry_count

    def apply_before(self, target):
        # Nothing to do here
        target = super(ExecutionRetryPolicyApplicator, self).apply_before(target=target)
        return target

    def apply_after(self, target):
        target = super(ExecutionRetryPolicyApplicator, self).apply_before(target=target)

        live_action_db = target
        retry_count = self._get_live_action_retry_count(live_action_db=live_action_db)

        extra = {'live_action_db': live_action_db, 'policy_ref': self._policy_ref,
                 'retry_on': self.retry_on, 'max_retry_count': self.max_retry_count,
                 'current_retry_count': retry_count}

        if live_action_db.status not in VALID_RETRY_STATUSES:
            # Currently we only support retrying on failed action
            LOG.debug('Liveaction not in a valid retry state, not checking retry policy',
                      extra=extra)
            return target

        if (retry_count + 1) > self.max_retry_count:
            LOG.info('Maximum retry count has been reached, not retrying', extra=extra)
            return target

        has_failed = live_action_db.status == LIVEACTION_STATUS_FAILED
        has_timed_out = live_action_db.status == LIVEACTION_STATUS_TIMED_OUT

        if has_failed and self.retry_on == RetryOnPolicy.FAILURE:
            extra['failure'] = True
            LOG.info('Policy matched, retrying action execution...', extra=extra)
            self._re_run_live_action(live_action_db=live_action_db)
            return target

        if has_timed_out and self.retry_on == RetryOnPolicy.TIMEOUT:
            extra['timeout'] = True
            LOG.info('Policy matched, retrying action execution...', extra=extra)
            self._re_run_live_action(live_action_db=live_action_db)
            return target

        return target

    def _get_live_action_retry_count(self, live_action_db):
        """
        Retrieve current retry count for the provided live action.

        :rtype: ``int``
        """
        context = getattr(live_action_db, 'context', {})
        retry_count = context.get('policies', {}).get('retry_count', 0)

        return retry_count

    def _re_run_live_action(self, live_action_db):
        retry_count = self._get_live_action_retry_count(live_action_db=live_action_db)

        # Add additional policy specific info to the context
        context = getattr(live_action_db, 'context', {})
        new_context = copy.deepcopy(context)
        new_context['policies'] = {
            'applied_policy': self._policy_ref,
            'retry_count': (retry_count + 1),
            'retried_liveaction_id': str(live_action_db.id)
        }

        action_ref = live_action_db.action
        parameters = live_action_db.parameters
        new_live_action_db = LiveActionDB(action=action_ref, parameters=parameters,
                                          context=new_context)
        _, action_execution_db = action_services.request(new_live_action_db)
        return action_execution_db
