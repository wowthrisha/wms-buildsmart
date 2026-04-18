import json
import redis
import time
from flask import Blueprint, Response, stream_with_context, current_app
from flask_login import login_required, current_user

bp = Blueprint('notifications', __name__)

@bp.route('/notifications/stream')
@login_required
def stream():
    def generate():
        r = redis.from_url(current_app.config.get('REDIS_URL', 'redis://localhost:6379/0'))
        pubsub = r.pubsub()
        channel = f'user:{current_user.id}:notifs'
        pubsub.subscribe(channel)

        # Initial keep-alive
        yield f"data: {json.dumps({'type': 'ping'})}\n\n"

        last_heartbeat = time.time()
        try:
            while True:
                now = time.time()
                if now - last_heartbeat >= 30:
                    yield ': heartbeat\n\n'
                    last_heartbeat = now

                message = pubsub.get_message(timeout=1)
                if message and message['type'] == 'message':
                    yield f"data: {message['data'].decode('utf-8')}\n\n"
                    last_heartbeat = time.time()
        except Exception as e:
            current_app.logger.error(f"SSE Stream Error: {e}")
        finally:
            pubsub.unsubscribe(channel)

    return Response(stream_with_context(generate()), mimetype='text/event-stream')
