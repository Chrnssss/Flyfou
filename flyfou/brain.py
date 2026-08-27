"""What to do next, decided from one snapshot and nothing else.

This module does not read the game, does not write to it, and does not sleep.
It takes a `World`, a set of `Rules` and the little state a fight needs, and
returns a `Plan`. That is deliberate: every one of the ten behaviours the bot is
supposed to have is a rule about which monster to pick, and rules about picking
are exactly the thing that is impossible to test through a screen and trivial to
test against a world you built by hand.

The order of the rules is itself a behaviour, and it is this:

  1. Something is hitting us. Nothing else matters while that is true - a bot
     that walks off to its next pull with a monster chewing on it dies, and it
     dies far from where anyone is watching.

  2. Something is hitting whoever we were told to protect. Same reasoning, one
     character over. This is the leech/RM case, and it outranks farming because
     the leech cannot defend itself; that is the whole point of a leech.

  3. We are already in a fight. Finish it. Switching targets mid-fight is how a
     bot ends up doing a quarter of the damage to four monsters instead of all
     of it to one, and it is what killstealing looks like from the other side.

  4. Pick something new, nearest first, out of what the rules allow.

  5. Nothing is allowed and nothing is near. Go home, or on to the next spot on
     the route.

Three of the rules seemed to need a thing this client does not have. Avoiding
somebody else's monster, hitting back, and defending a leech all sound like
questions about who is targeting whom, and there is no target field anywhere in
this build - it was searched for at length and it is not there.

They are not really questions about targeting though. They are questions about
who is being hurt, and health is a number this bot can watch:

  a monster losing health that we are not hitting is a monster in somebody
  else's fight, and taking it is stealing their kill;

  our own health going down while we are not fighting means something is
  hitting us;

  the leech's health going down means something is hitting the leech.

None of that needs a field the client does not keep. It needs the last tick's
health beside this one's, which `Memory` now carries.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .mem.world import PET, PLAYER, Entity, World

# What the bot is doing, for the log and the status line.
ATTACK = "attack"
TARGET = "target"
WALK = "walk"
REST = "rest"
IDLE = "idle"

#: Close enough to hit something without walking into it.
IN_REACH = 4.0

#: A fight that has not moved a health bar in this long is not a fight.
#:
#: Ten seconds was too patient. The loop above now re-clicks a target that is
#: not bleeding after a couple of seconds, so by the time this fires the fight
#: has already been given several chances and the monster is genuinely
#: unreachable - standing behind something, or on the far side of a wall.
STUCK_FIGHT = 6.0

#: Having arrived, this close counts as being there.
ARRIVED = 3.0

#: A monster that lost health this recently is in a fight with somebody.
CLAIMED = 4.0

#: Damage taken this recently still counts as being under attack.
UNDER_ATTACK = 3.0

#: How far a thing can be and still plausibly be what just hit us.
SWINGING_RANGE = 12.0


@dataclass
class Rules:
    """The profile, as the brain sees it. Nothing here knows about YAML."""

    levels: Optional[Tuple[int, int]] = None      # inclusive, or any level
    names: Set[str] = field(default_factory=set)  # empty means any name
    monster_kinds: Set[int] = field(default_factory=set)  # empty means "not a
                                                          # player and not a pet"
    radius: float = 0.0                  # 0 means do not limit the farm
    origin: Optional[Tuple[float, float, float]] = None
    route: List[Tuple[float, float, float]] = field(default_factory=list)
    protect: str = ""                    # character name to defend, or none
    avoid_killsteal: bool = True
    self_defence: bool = True
    rest_below: float = 0.35             # fraction of health to stop fighting at
    attack_key: str = "1"

    def wants(self, entity: Entity) -> bool:
        """Is this something we are allowed to attack?

        Kind cannot answer this on its own, and pretending otherwise is how a
        bot ends up beating up somebody's pet. This client files pets and wild
        monsters under the SAME kind value - `Smilodon Bestial` and `Dragon du
        rubis` are both 18 - so the only thing kind reliably rules out is other
        players.

        What does separate them is the profile: a pet is level one and a monster
        is not, and a pet is not on anybody's list of things to farm. So one of
        those two has to be set, and with neither the honest answer is to attack
        nothing rather than to guess.
        """
        if entity.kind == PLAYER and not self.monster_kinds:
            return False
        if self.monster_kinds and entity.kind not in self.monster_kinds:
            return False
        if not entity.alive or not entity.name:
            return False
        if not (self.levels or self.names or self.monster_kinds):
            return False
        if self.names and entity.name not in self.names:
            return False
        if self.levels and not (self.levels[0] <= entity.level <= self.levels[1]):
            return False
        return True


@dataclass
class Plan:
    """One decision, with the reason it was made spelled out."""

    do: str = IDLE
    entity: Optional[Entity] = None
    spot: Optional[Tuple[float, float, float]] = None
    why: str = ""

    def __repr__(self) -> str:
        who = f" {self.entity.name}#{self.entity.id}" if self.entity else ""
        where = (" (%.0f, %.0f, %.0f)" % self.spot) if self.spot else ""
        return f"<{self.do}{who}{where}: {self.why}>"


@dataclass
class Memory:
    """The little that cannot be seen in a single snapshot."""

    target_id: int = 0
    engaged_at: float = 0.0
    target_hp: int = 0
    hp_moved_at: float = 0.0
    route_step: int = 0
    kills: int = 0
    started: float = field(default_factory=time.time)
    give_up: Dict[int, float] = field(default_factory=dict)   # id -> until when

    # What health was doing last time we looked. This is how the bot knows who
    # is fighting whom on a client that does not record it.
    seen_hp: Dict[int, int] = field(default_factory=dict)
    bled_at: Dict[int, float] = field(default_factory=dict)   # id -> when
    my_hp: int = 0
    hurt_at: float = 0.0
    ward_hp: int = 0
    ward_hurt_at: float = 0.0

    @property
    def runtime(self) -> float:
        return time.time() - self.started

    @property
    def kills_per_hour(self) -> float:
        hours = self.runtime / 3600.0
        return self.kills / hours if hours > 0.001 else 0.0

    def abandon(self, entity_id: int, now: float, seconds: float = 25.0) -> None:
        self.give_up[entity_id] = now + seconds
        self.target_id = 0

    def sulking(self, entity_id: int, now: float) -> bool:
        until = self.give_up.get(entity_id)
        return bool(until and until > now)

    def observe(self, world: "World", now: float, ward: str = "") -> None:
        """Notice who lost health since last time, which is who is in a fight.

        Called once a tick before deciding anything. Both dictionaries are
        rebuilt or pruned every time rather than grown, because entity ids churn
        constantly as things spawn and despawn and a bot is meant to run for
        hours.
        """
        fresh: Dict[int, int] = {}
        for entity in world.entities:
            if not entity.id:
                continue
            was = self.seen_hp.get(entity.id)
            if was is not None and entity.hp < was:
                self.bled_at[entity.id] = now
            fresh[entity.id] = entity.hp
        self.seen_hp = fresh
        self.bled_at = {who: when for who, when in self.bled_at.items()
                        if now - when <= CLAIMED}

        me = world.me
        if me is not None:
            if self.my_hp and me.hp < self.my_hp:
                self.hurt_at = now
            self.my_hp = me.hp

        if ward:
            leech = world.named(ward)
            if leech is not None:
                if self.ward_hp and leech.hp < self.ward_hp:
                    self.ward_hurt_at = now
                self.ward_hp = leech.hp

    def bleeding(self, entity_id: int, now: float) -> bool:
        when = self.bled_at.get(entity_id)
        return bool(when and now - when <= CLAIMED)


def _ground(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(a[0] - b[0], a[2] - b[2])


def _claimed_by_someone(world: World, monster: Entity, me: Entity,
                        memory: "Memory", friends: Set[str],
                        now: float) -> bool:
    """Is this monster already in a fight that is not ours?

    Two ways to know. If the client records targets, whoever holds this
    monster's id is fighting it - this build does not, so that finds nothing.
    What always works is that it is losing health and we are not the cause: we
    know exactly what we are hitting, so anything else bleeding is somebody
    else's.
    """
    for other in world.attacking(monster.id):
        if other.address == me.address or not other.is_player:
            continue
        if other.name not in friends:
            return True

    if monster.id == memory.target_id:
        return False                      # that blood is ours
    return memory.bleeding(monster.id, now)


def choose(world: World, rules: Rules, memory: Memory,
           now: Optional[float] = None) -> Plan:
    """The whole state machine, as one decision over one snapshot."""
    now = time.time() if now is None else now
    me = world.me
    if me is None:
        return Plan(IDLE, why="our own character is not in the snapshot")

    friends = {rules.protect} if rules.protect else set()

    # 1. our own skin -------------------------------------------------------
    if me.max_hp and me.hp < rules.rest_below * me.max_hp:
        return Plan(REST, why="health is %d of %d, below the rest threshold"
                              % (me.hp, me.max_hp))

    if rules.self_defence and now - memory.hurt_at <= UNDER_ATTACK:
        # Something is hitting us. If we are already in a fight it is almost
        # certainly what we are fighting, so this only steps in when we are not.
        engaged = world.find(memory.target_id) if memory.target_id else None
        if engaged is None or not engaged.alive:
            attacker = _nearest_threat(world, me, rules, me.pos)
            if attacker is not None:
                return _engage(attacker, me, memory, now,
                               "something is hitting us")

    # 2. the leech ----------------------------------------------------------
    if rules.protect and now - memory.ward_hurt_at <= UNDER_ATTACK:
        ward = world.named(rules.protect)
        if ward is not None:
            attacker = _nearest_threat(world, me, rules, ward.pos)
            if attacker is not None:
                return _engage(attacker, me, memory, now,
                               "something is hitting " + rules.protect)

    # 3. the fight we are already in ---------------------------------------
    if memory.target_id:
        current = world.find(memory.target_id)
        if current is not None and current.alive:
            if current.hp != memory.target_hp:
                memory.target_hp = current.hp
                memory.hp_moved_at = now
            elif now - memory.hp_moved_at > STUCK_FIGHT:
                memory.abandon(current.id, now)
                return Plan(IDLE, entity=current,
                            why="its health has not moved in %.0fs, so this is "
                                "not a fight" % STUCK_FIGHT)
            return _engage(current, me, memory, now, "already fighting it")
        memory.target_id = 0

    # 4. something new ------------------------------------------------------
    pick, why = _pick_monster(world, rules, memory, me, friends, now)
    if pick is not None:
        return _engage(pick, me, memory, now, why)

    # 5. nowhere to be ------------------------------------------------------
    return _wander(rules, memory, me, why)


def _nearest_threat(world: World, me: Entity, rules: Rules,
                    around: Sequence[float]) -> Optional[Entity]:
    """The likeliest culprit for damage taken near a place.

    Without a target field nobody can say which monster swung, so the answer is
    the nearest one that could have: close enough to be in range of whoever got
    hit, and something the rules would let us attack anyway. Guessing the wrong
    neighbour costs one wasted fight; not hitting back at all costs the run.
    """
    best, closest = None, None
    for entity in world.others():
        if not rules.wants(entity):
            continue
        apart = _ground(entity.pos, around)
        if apart > SWINGING_RANGE:
            continue
        if closest is None or apart < closest:
            best, closest = entity, apart
    return best


def _engage(monster: Entity, me: Entity, memory: Memory, now: float,
            why: str) -> Plan:
    """Walk into range if we must, then hit it."""
    if memory.target_id != monster.id:
        memory.target_id = monster.id
        memory.engaged_at = now
        memory.target_hp = monster.hp
        memory.hp_moved_at = now
    if monster.apart_from(me) > IN_REACH:
        return Plan(WALK, entity=monster, spot=monster.pos,
                    why=why + ", closing to reach")
    return Plan(ATTACK, entity=monster, why=why)


def _pick_monster(world: World, rules: Rules, memory: Memory, me: Entity,
                  friends: Set[str], now: float):
    """Nearest allowed monster, with the reasons for every rejection kept."""
    home = rules.origin or me.pos
    rejected: Dict[str, int] = {}

    def no(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    best, closest = None, None
    for entity in world.others():
        if not rules.wants(entity):
            no("not the kind of thing we farm")
            continue
        if memory.sulking(entity.id, now):
            no("given up on recently")
            continue
        if rules.radius and _ground(entity.pos, home) > rules.radius:
            no("outside the farming radius")
            continue
        if rules.avoid_killsteal and _claimed_by_someone(
                world, entity, me, memory, friends, now):
            no("already losing health to somebody else")
            continue
        apart = entity.apart_from(me)
        if closest is None or apart < closest:
            best, closest = entity, apart

    if best is not None:
        return best, "nearest of what the rules allow, %.0f away" % closest
    if not rejected:
        return None, "nothing is on the map"
    worst = sorted(rejected.items(), key=lambda kv: -kv[1])
    return None, "nothing to fight: " + ", ".join(
        "%d %s" % (count, reason) for reason, count in worst)


def _wander(rules: Rules, memory: Memory, me: Entity, why: str) -> Plan:
    """With nothing to fight, go back where we started or on to the next spot."""
    if rules.route:
        spot = rules.route[memory.route_step % len(rules.route)]
        if _ground(me.pos, spot) <= ARRIVED:
            memory.route_step += 1
            spot = rules.route[memory.route_step % len(rules.route)]
            return Plan(WALK, spot=spot,
                        why=why + "; moving on to spot %d of %d"
                            % (memory.route_step % len(rules.route) + 1,
                               len(rules.route)))
        return Plan(WALK, spot=spot, why=why + "; heading for the next spot")

    if rules.origin and _ground(me.pos, rules.origin) > ARRIVED:
        return Plan(WALK, spot=rules.origin, why=why + "; returning to origin")
    return Plan(IDLE, why=why)
