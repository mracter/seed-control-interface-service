FROM praekeltfoundation/django-bootstrap
ENV DJANGO_SETTINGS_MODULE "seed_control_interface_service.settings"
RUN ./manage.py collectstatic --noinput
ENV APP_MODULE "seed_control_interface_service.wsgi:application"
