"""Lỗi nghiệp vụ dùng chung. Router chuyển các lỗi này thành HTTP 4xx."""


class BusinessError(Exception):
    """Vi phạm quy tắc nghiệp vụ (HTTP 400)."""

    status_code = 400

    def __init__(self, message: str, *, code: str = "business_error"):
        super().__init__(message)
        self.message = message
        self.code = code


class NotFoundError(BusinessError):
    status_code = 404

    def __init__(self, what: str, key):
        super().__init__(f"Không tìm thấy {what}: {key}", code="not_found")


class DuplicateError(BusinessError):
    status_code = 409

    def __init__(self, message: str):
        super().__init__(message, code="duplicate")


class PeriodLockedError(BusinessError):
    status_code = 423

    def __init__(self, partner_name: str, period: str):
        super().__init__(
            f"Kỳ {period} của khách '{partner_name}' đã chốt bảng kê (đã xác nhận). Mở khóa bảng kê trước khi sửa.",
            code="period_locked",
        )


class StockError(BusinessError):
    def __init__(self, message: str):
        super().__init__(message, code="insufficient_stock")


class LockedError(BusinessError):
    """Chứng từ / kỳ đã khóa (BBĐCCN RECONCILED, đơn đã duyệt...) – HTTP 409 Conflict."""

    status_code = 409

    def __init__(self, message: str):
        super().__init__(message, code="locked")


class PermissionDeniedError(BusinessError):
    status_code = 403

    def __init__(self, message: str):
        super().__init__(message, code="forbidden")
