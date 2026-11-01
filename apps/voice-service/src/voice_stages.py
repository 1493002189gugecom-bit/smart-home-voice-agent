"""Safe public timings for one turn; never contains speech or provider payloads."""
import time


class TurnStages:
    def __init__(self, publish, turn_id, input_mode, clock=time.monotonic):
        self.publish, self.turn_id, self.input_mode = publish, turn_id, input_mode
        self.clock = clock
        self.stage = None
        self.started = clock()

    def start(self, stage):
        self.finish()
        self.stage, self.started = stage, self.clock()
        self.publish("voice_stage", turn_id=self.turn_id, input_mode=self.input_mode,
                     stage=stage, status="running")

    def finish(self, status="completed"):
        if self.stage:
            self.publish("voice_stage", turn_id=self.turn_id, input_mode=self.input_mode,
                         stage=self.stage, status=status,
                         duration_ms=round((self.clock()-self.started)*1000))
            self.stage = None
