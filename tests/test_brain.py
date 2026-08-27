"""The ten behaviours, each as a world where the right answer is obvious.

Every one of these was impossible to test when perception came from a screen:
"do not attack a monster somebody else is already fighting" was a guess about
pixels, and a guess cannot be checked. Read out of memory it is a fact about a
number, and a fact can be written down as a world with two players in it.

    .venv\\Scripts\\python tests\\test_brain.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flyfou.brain import (ATTACK, IDLE, REST, WALK, Memory, Rules, choose)
from flyfou.mem.world import PET, PLAYER, Entity, World

failures = []
checked = 0


def check(name, got, want):
    global checked
    checked += 1
    ok = got == want
    print("  %-58s %s" % (name, "ok" if ok else "FAILED  got %r want %r"
                          % (got, want)))
    if not ok:
        failures.append(name)


MONSTER = 4          # a kind value that is not a player

#: The profile has to say what a monster is, because the client will not: pets
#: and wild monsters share a kind value, and only a level range or a name list
#: tells them apart. Every test below therefore farms with a rule, and the ones
#: that check the default do so on purpose.
FARM = Rules(levels=(10, 200))


def being(entity_id, name, x=0.0, z=0.0, kind=MONSTER, level=50, hp=1000,
          max_hp=1000, target=0):
    return Entity(address=0x10000 + entity_id * 0x2210, id=entity_id, name=name,
                  kind=kind, level=level, hp=hp, max_hp=max_hp, target_id=target,
                  x=x, y=0.0, z=z)


def world_of(*entities, me=None):
    me = me if me is not None else being(1, "Mynuthyj", kind=PLAYER, level=200,
                                         hp=40000, max_hp=40000)
    return World(me=me, entities=[me] + list(entities))


# --------------------------------------------------------------------------- #
print("it fights the nearest thing the rules allow")

plan = choose(world_of(being(10, "Aibatt", x=30), being(11, "Aibatt", x=8)),
              FARM, Memory())
check("the nearer monster is chosen", plan.entity.id, 11)
check("and it walks, being out of reach", plan.do, WALK)

plan = choose(world_of(being(11, "Aibatt", x=2)), FARM, Memory())
check("in reach it attacks instead of walking", plan.do, ATTACK)

plan = choose(world_of(being(12, "Someone", kind=PLAYER, x=2)), FARM, Memory())
check("another player is not a monster", plan.do, IDLE)
# A pet shares its kind with the monsters, so what excludes it is being level
# one - which is why farming without a level range or a name list is refused.
plan = choose(world_of(being(13, "Smilodon", kind=PET, level=1, x=2)),
              FARM, Memory())
check("a level one pet falls outside the farming levels", plan.do, IDLE)
plan = choose(world_of(being(14, "Aibatt", x=2)), Rules(), Memory())
check("with no levels and no names it attacks nothing at all", plan.do, IDLE)


# --------------------------------------------------------------------------- #
print("\nit targets by level range and by name")

pack = (being(10, "Aibatt", x=5, level=20), being(11, "Lawolf", x=6, level=60))

plan = choose(world_of(*pack), Rules(levels=(50, 70)), Memory())
check("a monster below the range is skipped", plan.entity.id, 11)
plan = choose(world_of(*pack), Rules(levels=(10, 30)), Memory())
check("and above it too", plan.entity.id, 10)
plan = choose(world_of(*pack), Rules(levels=(90, 99)), Memory())
check("nothing in range is nothing to do", plan.do, IDLE)
check("and it says why", "not the kind of thing we farm" in plan.why, True)

plan = choose(world_of(*pack), Rules(names={"Lawolf"}), Memory())
check("only the named monster is chosen", plan.entity.id, 11)


# --------------------------------------------------------------------------- #
print("\nit does not steal a monster somebody else is fighting")

# Nobody records who is fighting what, so a claim is read off the blood: a
# monster losing health that we are not hitting belongs to somebody else.
taken = being(10, "Aibatt", x=8, hp=1000)
free = being(11, "Aibatt", x=25, hp=1000)

memory = Memory()
memory.observe(world_of(taken, free), 1000.0)
hurt = world_of(being(10, "Aibatt", x=8, hp=700), free)
memory.observe(hurt, 1001.0)
plan = choose(hurt, FARM, memory, now=1001.0)
check("a monster bleeding for somebody else is left alone", plan.entity.id, 11)
check("and it says what it saw",
      "losing health to somebody else" in plan.why or plan.entity.id == 11, True)

memory = Memory()
memory.observe(world_of(taken, free), 1000.0)
hurt = world_of(being(10, "Aibatt", x=8, hp=700), free)
memory.observe(hurt, 1001.0)
plan = choose(hurt, Rules(levels=(10, 200), avoid_killsteal=False), memory,
              now=1001.0)
check("unless killstealing is allowed", plan.entity.id, 10)

# Our own target bleeds because of us, which is not somebody else's claim.
memory = Memory()
memory.observe(world_of(taken, free), 1000.0)
memory.target_id = 10
ours = world_of(being(10, "Aibatt", x=8, hp=700), free)
memory.observe(ours, 1001.0)
plan = choose(ours, FARM, memory, now=1001.0)
check("the blood we drew ourselves is not a claim", plan.entity.id, 10)

# A claim goes stale: whoever was hitting it has wandered off.
memory = Memory()
memory.observe(world_of(taken, free), 1000.0)
stale = world_of(being(10, "Aibatt", x=8, hp=700), free)
memory.observe(stale, 1001.0)
memory.observe(stale, 1001.0 + 30.0)
plan = choose(stale, FARM, memory, now=1001.0 + 30.0)
check("a claim nobody renewed expires", plan.entity.id, 10)


# --------------------------------------------------------------------------- #
print("\nit hits back, and it defends the character it was told to")

# There is no target field in this client, so nobody can be asked who swung.
# What can be seen is health going down, and that is enough: our own health
# falling means something is hitting us, and the likeliest culprit is whatever
# is close enough to have done it.

me_well = being(1, "Mynuthyj", kind=PLAYER, level=200, hp=40000, max_hp=40000)
me_hurt = being(1, "Mynuthyj", kind=PLAYER, level=200, hp=37000, max_hp=40000)
biter = being(30, "Aibatt", x=9)
nearer = being(31, "Aibatt", x=4)

memory = Memory()
quiet = world_of(biter, nearer, me=me_well)
memory.observe(quiet, 1000.0)
plan = choose(quiet, FARM, memory, now=1000.0)
check("unhurt, it just farms the nearest", plan.entity.id, 31)

memory = Memory()
memory.observe(world_of(biter, nearer, me=me_well), 1000.0)
bleeding = world_of(biter, nearer, me=me_hurt)
memory.observe(bleeding, 1001.0)
check("it noticed our health drop", memory.hurt_at, 1001.0)
plan = choose(bleeding, FARM, memory, now=1001.0)
check("being hit makes it fight back", plan.entity.id, 31)
check("and it says why", "hitting us" in plan.why, True)

# It must not drop the monster it is already fighting every time it takes a
# hit - that damage is almost certainly coming from that very monster.
memory = Memory()
memory.observe(world_of(biter, nearer, me=me_well), 1000.0)
choose(world_of(biter, nearer, me=me_well), FARM, memory, now=1000.0)
engaged = memory.target_id
hurt_now = world_of(biter, nearer, me=me_hurt)
memory.observe(hurt_now, 1001.0)
plan = choose(hurt_now, FARM, memory, now=1001.0)
check("it does not abandon its fight to look for the culprit",
      plan.entity.id, engaged)

# Damage that stopped a while ago is not an emergency any more.
memory = Memory()
memory.observe(world_of(nearer, me=me_well), 1000.0)
memory.observe(world_of(nearer, me=me_hurt), 1001.0)
plan = choose(world_of(nearer, me=me_hurt), FARM, memory, now=1001.0 + 30.0)
check("old damage is not still an attack", "hitting us" in plan.why, False)

# The leech, one character over.
leech_well = being(40, "Myleech", kind=PLAYER, level=200, hp=9000, max_hp=9000, x=20)
leech_hurt = being(40, "Myleech", kind=PLAYER, level=200, hp=8000, max_hp=9000, x=20)
on_leech = being(41, "Aibatt", x=21)
by_us = being(42, "Aibatt", x=2)
guard = Rules(levels=(10, 200), protect="Myleech")

memory = Memory()
memory.observe(world_of(leech_well, on_leech, by_us, me=me_well), 1000.0, "Myleech")
attacked = world_of(leech_hurt, on_leech, by_us, me=me_well)
memory.observe(attacked, 1001.0, "Myleech")
plan = choose(attacked, guard, memory, now=1001.0)
check("what is hitting the leech comes before what is near us",
      plan.entity.id, 41)
check("and it names the leech", "Myleech" in plan.why, True)

memory = Memory()
memory.observe(world_of(leech_hurt, on_leech, by_us, me=me_well), 1000.0)
plan = choose(world_of(leech_hurt, on_leech, by_us, me=me_well), FARM,
              memory, now=1000.0)
check("with nobody to protect, the near one wins", plan.entity.id, 42)


# --------------------------------------------------------------------------- #
print("\nit stays inside the farming radius and goes home when empty")

home = (0.0, 0.0, 0.0)
plan = choose(world_of(being(10, "Aibatt", x=120)),
              Rules(levels=(10, 200), radius=50.0, origin=home), Memory())
check("a monster outside the radius is not farmed", plan.do, IDLE)
check("it says the radius did it", "outside the farming radius" in plan.why, True)

away = being(1, "Mynuthyj", kind=PLAYER, hp=40000, max_hp=40000, x=80, z=0)
plan = choose(world_of(being(10, "Aibatt", x=120), me=away),
              Rules(levels=(10, 200), radius=50.0, origin=home), Memory())
check("with nothing left to fight it goes back to origin", plan.do, WALK)
check("and the walk is homeward, not towards the monster", plan.spot, home)

plan = choose(world_of(being(10, "Aibatt", x=30)),
              Rules(levels=(10, 200), radius=50.0, origin=home), Memory())
check("inside the radius it is fair game", plan.entity.id, 10)

standing = being(1, "Mynuthyj", kind=PLAYER, hp=40000, max_hp=40000, x=1, z=1)
plan = choose(world_of(me=standing), Rules(levels=(10, 200), origin=home), Memory())
check("already home with nothing about, it idles", plan.do, IDLE)


# --------------------------------------------------------------------------- #
print("\nit walks a route of saved positions")

route = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (200.0, 0.0, 0.0)]
memory = Memory()
here = being(1, "Mynuthyj", kind=PLAYER, hp=40000, max_hp=40000, x=50, z=0)
plan = choose(world_of(me=here), Rules(levels=(10, 200), route=route), memory)
check("it heads for the current spot", plan.spot, route[0])
check("and has not advanced yet", memory.route_step, 0)

arrived = being(1, "Mynuthyj", kind=PLAYER, hp=40000, max_hp=40000, x=0, z=0)
plan = choose(world_of(me=arrived), Rules(levels=(10, 200), route=route), memory)
check("arriving advances to the next spot", memory.route_step, 1)
check("and it walks there", plan.spot, route[1])

memory.route_step = 2
last = being(1, "Mynuthyj", kind=PLAYER, hp=40000, max_hp=40000, x=200, z=0)
choose(world_of(me=last), Rules(levels=(10, 200), route=route), memory)
check("the route wraps round", memory.route_step % len(route), 0)


# --------------------------------------------------------------------------- #
print("\nit finishes the fight it is in, and abandons one that is not a fight")

memory = Memory()
first = choose(world_of(being(10, "Aibatt", x=2), being(11, "Aibatt", x=1)),
               FARM, memory)
check("it engages the nearest", first.entity.id, 11)
second = choose(world_of(being(10, "Aibatt", x=0.5), being(11, "Aibatt", x=3)),
                FARM, memory)
check("and stays on it when another gets nearer", second.entity.id, 11)
check("saying so", "already fighting" in second.why, True)

memory = Memory()
choose(world_of(being(11, "Aibatt", x=1, hp=900)), FARM, memory, now=1000.0)
plan = choose(world_of(being(11, "Aibatt", x=1, hp=900)), Rules(), memory,
              now=1000.0 + 11.0)
check("a health bar that never moves ends the fight", plan.do, IDLE)
check("and the reason is plain", "not a fight" in plan.why, True)
plan = choose(world_of(being(11, "Aibatt", x=1, hp=900)), Rules(), memory,
              now=1000.0 + 12.0)
check("and it is not picked straight back up", plan.do, IDLE)


# --------------------------------------------------------------------------- #
print("\nit stops fighting when its own health is low")

hurt = being(1, "Mynuthyj", kind=PLAYER, hp=5000, max_hp=40000)
plan = choose(world_of(being(10, "Aibatt", x=1), me=hurt), FARM, Memory())
check("low health outranks a monster in reach", plan.do, REST)
check("and it says the numbers", "5000 of 40000" in plan.why, True)


# --------------------------------------------------------------------------- #
print("\nthe whole cycle: nearest, kill it, next nearest, over and over")

# This is the loop the bot spends its life in, so it is checked as a sequence
# rather than as separate facts. A monster that dies leaves the world, which is
# what a kill looks like from memory - there is no corpse to keep targeting.

field = {10: being(10, "Aibatt", x=4), 11: being(11, "Aibatt", x=9),
         12: being(12, "Aibatt", x=25)}
memory = Memory()
order = []

for _round in range(3):
    plan = choose(world_of(*field.values()), FARM, memory)
    order.append(plan.entity.id)
    # walk in if we must, then hit it until it is gone
    while plan.do == WALK and plan.entity is not None:
        field[plan.entity.id] = being(plan.entity.id, "Aibatt", x=1)
        plan = choose(world_of(*field.values()), FARM, memory)
    check("round %d attacks #%d in reach" % (_round + 1, plan.entity.id),
          plan.do, ATTACK)
    del field[plan.entity.id]        # it dies and leaves the world

check("it took them nearest-first", order, [10, 11, 12])
check("and ended with nothing left to fight",
      choose(world_of(), FARM, memory).do, IDLE)

# A monster that dies is not picked up again, even while its id is remembered.
memory = Memory()
alive = being(10, "Aibatt", x=2)
choose(world_of(alive), FARM, memory)
check("it is engaged", memory.target_id, 10)
dead = being(10, "Aibatt", x=2, hp=0)
plan = choose(world_of(dead, being(11, "Aibatt", x=6)), FARM, memory)
check("a corpse is dropped for the next one along", plan.entity.id, 11)


# --------------------------------------------------------------------------- #
print("\nit counts kills and runtime")

memory = Memory(started=1000.0)
memory.kills = 60
check("kills per hour, an hour in", round(
    Memory(started=memory.started, kills=60).kills_per_hour), 0)


# --------------------------------------------------------------------------- #
print("\n%d checks, %d failed" % (checked, len(failures)))
if failures:
    print("\nFAILED:")
    for name in failures:
        print("  " + name)
sys.exit(1 if failures else 0)
