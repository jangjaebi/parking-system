
from gpiozero import DistanceSensor
from time import sleep

# TRIG: GPIO23, ECHO: GPIO24
sensor = DistanceSensor(
    echo=24,
    trigger=23,
    max_distance=2
)

try:
    while True:
        distance = sensor.distance * 100

        print(f"측정 거리: {distance:.1f} cm")

        if distance < 15:
            print("차량 접근 감지!")
        else:
            print("차량 없음")

        sleep(0.5)

except KeyboardInterrupt:
    print("센서 종료")

finally:
    sensor.close()