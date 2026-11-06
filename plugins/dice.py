"""Example plugin. Any .py file in plugins\\ is loaded at start-up and again whenever it changes."""
import random
from typing import Literal

from grain import tool


@tool(keywords=["dice", "die", "roll", "throw", "d4", "d6", "d8", "d10", "d12", "d20"],
      phrases=["dice", "roll dice", "roll a die", "roll the dice", "roll {count} dice",
               "roll a {sides} sided die", "roll {count} {sides} sided dice", "roll {count} {sides} sided die"])
def roll_dice(count: int = 1, sides: Literal["4", "6", "8", "10", "12", "20"] = "6"):
    "Roll one or more dice and report the total."
    rolls = [random.randint(1, int(sides)) for _ in range(max(1, min(int(count), 20)))]
    return f"rolled {rolls} = {sum(rolls)}"
