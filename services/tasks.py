import json
import uuid
import requests

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from celery.task import Task
from celery.utils.log import get_task_logger
from celery.exceptions import SoftTimeLimitExceeded

from .models import Service, Status, UserServiceToken
from dashboards.models import WidgetData


logger = get_task_logger(__name__)


class DeliverHook(Task):
    def run(self, target, payload, instance_id=None, hook_id=None, **kwargs):
        """
        target:     the url to receive the payload.
        payload:    a python primitive data structure
        instance_id:   a possibly None "trigger" instance ID
        hook_id:       the ID of defining Hook object
        """
        requests.post(
            url=target,
            data=json.dumps(payload),
            headers={
                'Content-Type': 'application/json',
                'Authorization': 'Token %s' % settings.HOOK_AUTH_TOKEN
            }
        )


def deliver_hook_wrapper(target, payload, instance, hook):
    if instance is not None:
        if isinstance(instance.id, uuid.UUID):
            instance_id = str(instance.id)
        else:
            instance_id = instance.id
    else:
        instance_id = None
    kwargs = dict(target=target, payload=payload,
                  instance_id=instance_id, hook_id=hook.id)
    DeliverHook.apply_async(kwargs=kwargs)


class QueuePollService(Task):
    def run(self):
        """
        Queues all services to be polled. Should be run via beat.
        """
        services = Service.objects.all()
        for service in services:
            poll_service.apply_async(kwargs={"service_id": str(service.id)})
        return "Queued <%s> Service(s) for Polling" % services.count()

queue_poll_service = QueuePollService()


class PollService(Task):

    """
    Task to check the status of a service
    """
    name = "services.tasks.poll_service"

    class FailedEventRequest(Exception):

        """
        The attempted task failed because of a non-200 HTTP return
        code.
        """

    def get_health(self, url, token=None):
        url = "%s/api/health/" % (url, )
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = "Token %s" % (token,)
        r = requests.get(url, headers=headers)
        return r

    def run(self, service_id, **kwargs):
        """
        Retrieve a status from remote service. Set the up/down state and log.
        """
        l = self.get_logger(**kwargs)

        l.info("Loading Service for healthcheck")
        try:
            service = Service.objects.get(id=service_id)
            l.info("Getting health for <%s>" % (service.name))
            status = self.get_health(service.url, service.token)
            try:
                result = status.json()
                service.up = result["up"]
                service.save()
                Status.objects.create(
                    service=service,
                    up=result["up"],
                    result=result["result"]
                )
                l.info("Service <%s> up: <%s>" % (service.name, service.up))
            except:
                # can't decode means there was not a valid response
                l.info("Failed to parse response from <%s>" % (service.name))
                Status.objects.create(
                    service=service,
                    up=False,
                    result={"error": "No parseable response from service"}
                )
                service.up = False
                service.save()
            return "Completed healthcheck for <%s>" % (service.name)
        except ObjectDoesNotExist:
            logger.error('Missing Service', exc_info=True)

        except SoftTimeLimitExceeded:
            logger.error(
                'Soft time limit exceed processing poll of service \
                 via Celery.',
                exc_info=True)

poll_service = PollService()


class GetUserToken(Task):

    """
    Task to check or set the user tokens for a service
    """
    name = "services.tasks.get_user_token"

    class FailedEventRequest(Exception):

        """
        The attempted task failed because of a non-200 HTTP return
        code.
        """

    def create_token(self, url, email, token=None):
        url = "%s/api/v1/user/token/" % (url, )
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = "Token %s" % (token,)
        data = {"email": email}
        r = requests.post(url, headers=headers, data=json.dumps(data))
        return r

    def run(self, service_id, user_id, email, **kwargs):
        """
        Create and Retrieve a token from remote service. Save to DB.
        """
        l = self.get_logger(**kwargs)

        l.info("Loading Service for token creation")
        try:
            service = Service.objects.get(id=service_id)
            l.info("Getting token for <%s> on <%s>" % (email, service.name))
            response = self.create_token(service.url, email, service.token)
            try:
                result = response.json()
                ust, created = UserServiceToken.objects.get_or_create(
                    service=service, user_id=user_id, email=email)
                ust.token = result["token"]
                ust.save()
                l.info("Token saved for <%s> on <%s>" % (email, service.name))
            except:
                # can't decode means there was not a valid response
                l.info("Failed to parse response from <%s>" % (service.name))
            return "Completed getting token for <%s>" % (email)
        except ObjectDoesNotExist:
            logger.error('Missing Service', exc_info=True)

        except SoftTimeLimitExceeded:
            logger.error(
                'Soft time limit exceed processing getting service token \
                 via Celery.',
                exc_info=True)

get_user_token = GetUserToken()


class QueueServiceMetricSync(Task):
    def run(self):
        """
        Queues all services to be polled for metrics. Should be run via beat.
        """
        services = Service.objects.all()
        for service in services:
            service_metric_sync.apply_async(
                kwargs={"service_id": str(service.id)})
        return "Queued <%s> Service(s) for Metric Sync" % services.count()

queue_service_metric_sync = QueueServiceMetricSync()


class ServiceMetricSync(Task):

    """
    Task to get the available metrics of a service
    """
    name = "services.tasks.service_metric_sync"

    class FailedEventRequest(Exception):

        """
        The attempted task failed because of a non-200 HTTP return
        code.
        """

    def get_metrics(self, url, token=None):
        url = "%s/api/metrics/" % (url, )
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = "Token %s" % (token,)
        r = requests.get(url, headers=headers)
        return r

    def run(self, service_id, **kwargs):
        """
        Retrieve a list of metrics. Ensure they are set as metric data sources.
        """
        l = self.get_logger(**kwargs)

        l.info("Loading Service for metric sync")
        try:
            service = Service.objects.get(id=service_id)
            l.info("Getting metrics for <%s>" % (service.name))
            metrics = self.get_metrics(service.url, service.token)
            result = metrics.json()
            if "metrics_available" in result:
                for key in result["metrics_available"]:
                    check = WidgetData.objects.filter(service=service, key=key)
                    if check.count() == 0:
                        WidgetData.objects.create(
                            service=service,
                            key=key,
                            title="TEMP - Pending update"
                        )
                        l.info("Add WidgetData for <%s>" % (key,))
            return "Completed metric sync for <%s>" % (service.name)
        except ObjectDoesNotExist:
            logger.error('Missing Service', exc_info=True)

        except SoftTimeLimitExceeded:
            logger.error(
                'Soft time limit exceed processing pull of service metrics \
                 via Celery.',
                exc_info=True)

service_metric_sync = ServiceMetricSync()
