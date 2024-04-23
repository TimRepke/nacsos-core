from server.pipelines.tasks import broker
from dramatiq_dashboard import DashboardApp

app = DashboardApp(broker=broker, prefix='')
