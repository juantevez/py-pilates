"""Prueba de carga: alumnos que miran la grilla, reservan y cancelan.

Uso:
    pip install locust
    locust -f tests/load/locustfile.py --host http://localhost:8000 \
        --users 40 --spawn-rate 5 --run-time 3m --headless
"""

import itertools
import random

from locust import HttpUser, between, task

PASSWORD = "loadtest-1234"
_ids = itertools.count(1)


class Alumno(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        n = next(_ids)
        email = f"load{n}@example.com"
        # 201 la primera vez, 409 si ya existe de una corrida anterior: ambos sirven.
        with self.client.post(
            "/auth/register",
            json={"email": email, "full_name": f"Load {n}", "password": PASSWORD},
            name="/auth/register",
            catch_response=True,
        ) as r:
            if r.status_code in (201, 409):
                r.success()
            else:
                r.failure(f"{r.status_code}: {r.text[:200]}")
        # Login una sola vez por usuario: bcrypt es caro y no queremos medir solo eso.
        r = self.client.post("/auth/login", json={"email": email, "password": PASSWORD})
        r.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    @task(5)
    def ver_disponibles(self):
        self.client.get("/clases/disponibles")

    @task(3)
    def mis_turnos(self):
        self.client.get("/turnos/mios")

    @task(1)
    def me(self):
        self.client.get("/auth/me")

    @task(2)
    def reservar_y_cancelar(self):
        dias = self.client.get("/clases/disponibles").json()
        clases = [c for d in dias for c in d["clases"] if c["puede_reservar"]]
        if not clases:
            return
        clase = random.choice(clases)
        # Con 40 usuarios compitiendo por cupos, un rechazo de negocio (clase llena,
        # ya reservado) es esperable; solo contamos como error los 5xx.
        with self.client.post(
            "/turnos", json={"clase_id": clase["clase_id"]}, catch_response=True
        ) as r:
            if r.status_code >= 500:
                r.failure(f"{r.status_code}: {r.text[:200]}")
                return
            r.success()
            if r.status_code != 201:
                return
            turno_id = r.json()["id"]
        # Cancelamos para no agotar los cupos y que la prueba sea repetible.
        self.client.post(f"/turnos/{turno_id}/cancelar", name="/turnos/[id]/cancelar")
