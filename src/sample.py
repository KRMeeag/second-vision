
def sample_pwm_esp32(depth_map_output):
    print("depth_map_output", depth_map_output)




def main():
    dp_output = {
        "left": 32,
        "right": 33,
        "top": 25,
    }

    sample_pwm_esp32(dp_output)