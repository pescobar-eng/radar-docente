import os
import urllib.request

RADAR_URL = os.environ["RADAR_URL"].rstrip("/")

url = RADAR_URL + "/api/actualizar"

request = urllib.request.Request(
    url,
    method="POST"
)

with urllib.request.urlopen(
    request,
    timeout=180
) as response:

    body = response.read().decode("utf-8")

    print("Radar actualizado correctamente.")
    print(body)
