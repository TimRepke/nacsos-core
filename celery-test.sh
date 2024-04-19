#!/bin/bash

export CELERY_BROKER_URL="redis://localhost:6379/0"
export CELERY_RESULT_BACKEND="redis://localhost:6379/0"
export FLOWER_UNAUTHENTICATED_API=true

source venv/bin/activate

function ctrl_c() {
  echo "Ctrl + C happened"
  # celery purge
  celery control shutdown --destination nacsos-pipes-1,nacsos-pipes-2,nacsos-pipes-3,nacsos-pipes-4,nacsos-pipes-5,nacsos-pipes-6
  echo "Everything shut down"
  exit 0
}

celery control shutdown

celery -A server.pipelines.tasks worker -n nacsos-pipes-1 --loglevel info --task-events --concurrency=1 --pool solo --queues nacsos_default --time-limit=86400 --detach --pidfile .tasks/celery-1.pid --logfile .tasks/celery.log
#celery -A server.pipelines.tasks worker -n nacsos-pipes-2  --loglevel info --task-events --concurrency=1 --pool solo --queues nacsos_default --time-limit=86400 --detach --pidfile .tasks/celery-2.pid --logfile .tasks/celery.log
#celery -A server.pipelines.tasks worker -n nacsos-pipes-3  --loglevel info --task-events --concurrency=1 --pool solo --queues nacsos_default --time-limit=86400 --detach --pidfile .tasks/celery-3.pid --logfile .tasks/celery.log
#celery -A server.pipelines.tasks worker -n nacsos-pipes-4 --loglevel info --task-events --concurrency=1 --pool solo --queues nacsos_default --time-limit=86400 --detach --pidfile .tasks/celery-4.pid --logfile .tasks/celery.log
#celery -A server.pipelines.tasks worker -n nacsos-pipes-5 --loglevel info --task-events --concurrency=1 --pool solo --queues nacsos_slow --time-limit=864000 --detach --pidfile .tasks/celery-5.pid --logfile .tasks/celery.log
celery -A server.pipelines.tasks worker -n nacsos-pipes-6 --loglevel info --task-events --concurrency=1 --pool solo --queues nacsos_slow --time-limit=864000 --detach --pidfile .tasks/celery-6.pid --logfile .tasks/celery.log

trap ctrl_c INT

celery -A server.pipelines.tasks flower
