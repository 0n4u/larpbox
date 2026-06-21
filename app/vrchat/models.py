from __future__ import annotations
from dataclasses import dataclass, field
from ..user_status import UserStatusInfo
_TRUST_RANKS: tuple[tuple[str, str, str, str], ...] = (('system_trust_legend', 'Legend', 'LEG', '#e8a040'), ('system_trust_veteran', 'Veteran', 'VET', '#c084fc'), ('system_trust_trusted', 'Trusted', 'TRU', '#4ea3ff'), ('system_trust_known', 'Known', 'KNW', '#7db87d'), ('system_trust_basic', 'User', 'USR', '#9aa0a6'))

@dataclass(frozen=True)
class TrustRank:
    label: str
    short: str
    color: str

@dataclass(frozen=True)
class AvatarResult:
    id: str
    name: str
    description: str
    author_name: str
    image_url: str
    performance: str | None = None
    author_id: str = ''
    platforms: tuple[str, ...] = ()

@dataclass(frozen=True)
class UserBadge:
    badge_id: str
    name: str
    image_url: str
    showcased: bool = False

@dataclass(frozen=True)
class FriendEntry:
    user_id: str
    display_name: str
    thumbnail_url: str
    trust: TrustRank
    status: UserStatusInfo
    badges: list[UserBadge] = field(default_factory=list)
    world_name: str | None = None
    player_count: int | None = None

    @property
    def is_online(self) -> bool:
        return self.status.is_online

    @property
    def can_join(self) -> bool:
        return self.status.key == 'join_me' and bool(self.status.location)

    @property
    def join_location(self) -> str | None:
        return self.status.location if self.can_join else None

@dataclass(frozen=True)
class InstancePlayer:
    user_id: str
    display_name: str
    thumbnail_url: str
    is_friend: bool = False
    trust: TrustRank | None = None
    avatar_id: str | None = None
    status: UserStatusInfo | None = None
    avatar_performance: str | None = None

@dataclass(frozen=True)
class InstanceInfo:
    world_id: str
    instance_id: str
    world_name: str
    player_count: int
    players: list[InstancePlayer]
    owner_id: str = ''
    can_close_instance: bool = False
    region: str = ''
    instance_type: str = ''
    location: str = ''
    owner_display_name: str = ''
    max_players: int | None = None

@dataclass(frozen=True)
class CurrentUserProfile:
    user_id: str
    display_name: str
    bio: str
    status_description: str
    image_url: str
    trust: TrustRank
    badges: list[UserBadge]
    status: UserStatusInfo
    world_name: str | None = None
    instance_label: str | None = None

def trust_rank_from_tags(tags: list[str] | None) -> TrustRank:
    tag_set = {str(tag) for tag in tags or []}
    for trust_tag, label, short, color in _TRUST_RANKS:
        if trust_tag in tag_set:
            return TrustRank(label=label, short=short, color=color)
    return TrustRank(label='Visitor', short='VIS', color='#666666')
