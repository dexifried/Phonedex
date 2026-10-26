"""Strict local metadata models. Validation errors must never echo input values."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)


class Password(Model):
    password: str = Field(min_length=1, max_length=1024)


class Agent(Model):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=500)
    prompt: str = Field(default='', max_length=16000)
    voice: str = Field(default='marin', max_length=80)
    model: str = Field(default='gpt-realtime', max_length=100)
    enabled: bool = True
    extension: str = Field(default='', max_length=12, pattern=r'^\d{0,12}$')
    tools: list[str] = Field(default_factory=list, max_length=20)
    mode: Literal['normal', 'business'] = 'normal'
    max_duration_seconds: int = Field(default=120, ge=20, le=300)


class Contact(Model):
    name: str = Field(min_length=1, max_length=100)
    number: str = Field(max_length=16, pattern=r'^\+[1-9][0-9]{6,14}$')
    company: str = Field(default='', max_length=150)
    notes: str = Field(default='', max_length=2000)
    allowed: bool = False
    tags: list[str] = Field(default_factory=list, max_length=20)


class Provider(Model):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal['sip', 'telnyx', 'twilio', 'signalwire', 'generic', 'worker', 'openai'] = 'sip'
    config: dict[str, str | int | bool] = Field(default_factory=dict, max_length=30)
    credentials: dict[str, str] = Field(default_factory=dict, max_length=10)
    enabled: bool = False

    @field_validator('credentials')
    @classmethod
    def credential_fields(cls, value):
        if any(k not in {'username', 'password', 'api_key', 'token', 'account_sid', 'auth_token'} or len(v) > 4096 for k, v in value.items()):
            raise ValueError('Unsupported credential field')
        return value


class Task(Model):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)
    contact_id: str | None = None
    agent_id: str | None = None
    status: Literal['draft', 'pending', 'completed', 'cancelled'] = 'draft'
    prompt: str = Field(default='', max_length=16000)


class Preflight(Model):
    contact_id: str = Field(min_length=1, max_length=100)
    agent_id: str = Field(min_length=1, max_length=100)
    duration_seconds: int = Field(default=120, ge=20, le=300)
    prompt: str = Field(default='', max_length=16000)
    profile_fields: list[dict[str,str]] = Field(default_factory=list,max_length=20)


class Dial(Model):
    approval_id: str = Field(min_length=1, max_length=100)
    confirm: bool = False


class Control(Model):
    kind: Literal['hangup', 'instruct', 'steer', 'private_instruction','profile_field', 'hold', 'mute', 'transfer', 'conference']
    payload: dict = Field(default_factory=dict)
    confirm: bool = False


class Profile(Model):
    name: str = Field(min_length=1, max_length=100)


class VaultField(Model):
    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=10000)
    sensitivity: str = Field(default='sensitive', max_length=30)


class Grant(Model):
    field_id: str = Field(min_length=1, max_length=100)
    call_id: str = Field(min_length=1, max_length=100)
    recipient: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=500)
    expires_seconds: int = Field(default=300, ge=1, le=300)
