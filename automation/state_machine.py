from enum import StrEnum


class State(StrEnum):
    HOME = "home"
    AUTHENTICATION = "authentication"
    CREATE = "create"
    UPLOAD = "upload"
    WAIT_UPLOAD = "wait_upload"
    SELECT_IMAGE = "select_image"
    MANUAL_ANIMATE = "manual_animate"
    INSERT_PROMPT = "insert_prompt"
    CLICK_ANIMATE = "click_animate"
    WAIT_RESULT = "wait_result"
    SUCCESS = "success"
    ERROR = "error"
    DEFERRED = "deferred"
    FAILED_FINAL = "failed_final"
    RATE_LIMIT = "rate_limit"
