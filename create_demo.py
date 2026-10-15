
import pyttsx3

script = """
The customer reports reduced driving range and high
battery temperature during charging.

The technician observed a battery temperature of
43 degrees Celsius.

I recommend checking the battery cooling system
and reviewing the battery management system logs.
"""

engine = pyttsx3.init()
engine.setProperty("rate", 145)
engine.save_to_file(script, "demo_inspection.wav")
engine.runAndWait()

print("Demo recording created: demo_inspection.wav")
