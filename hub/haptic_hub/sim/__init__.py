"""Simulated world for testing the whole system without glasses, phone or belt.

A ray-cast world renders what the glasses camera would see; a simulated phone
sends those frames over the real WebSocket protocol to the real hub; the hub's
commands go over the real wire format into the real belt firmware logic
(esp32/lib/belt_core, compiled for the host). See hub/README.md "Simulation".
"""
