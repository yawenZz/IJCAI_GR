# Concurrent Goal Recognition in Multi-agent Systems

The code is adapted from the source code for the IJCAI-23 paper *Multi-Agent Intention Recognition and Progression*, by Michael Dann, Yuan Yao, Natasha Alechina, Brian Logan, Felipe Meneguzzi and John Thangarajah, which can be found at https://github.com/mchldann/IJCAI_GR.

## Installing the Requirements

To install the Python requirements via Anaconda, use
```conda env create -f environment.yml```.

## Running

To recreate the results, run:

```python python_agent.py scenario_name```

where scenario_name is one of {neutral_1, neutral_2, neutral_3, neutral_4}.

Agent scores are automatically logged to results/cooperative_craft_world_dqn/ folder.
