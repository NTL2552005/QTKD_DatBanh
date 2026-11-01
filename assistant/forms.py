import uuid

from django import forms

from .ai import MAX_USER_MESSAGE_CHARS
from .models import StorePolicy


class ChatMessageForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    content = forms.CharField(
        label="Tin nhắn",
        max_length=MAX_USER_MESSAGE_CHARS,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": "Ví dụ: Mình cần bánh dâu cho sinh nhật...",
            }
        ),
    )

    def clean_content(self):
        content = self.cleaned_data["content"].strip()
        if not content:
            raise forms.ValidationError("Vui lòng nhập nội dung cần tư vấn.")
        return content


class StorePolicyForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["code"].disabled = True
            self.fields["code"].help_text = "Mã ổn định, không thể thay đổi sau khi tạo."

    class Meta:
        model = StorePolicy
        fields = ("code", "title", "content")
        labels = {
            "code": "Mã chính sách",
            "title": "Tiêu đề",
            "content": "Nội dung chính sách đề xuất",
        }
        widgets = {
            "content": forms.Textarea(attrs={"rows": 9}),
        }

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise forms.ValidationError("Vui lòng nhập tiêu đề.")
        return title

    def clean_content(self):
        content = self.cleaned_data["content"].strip()
        if not content:
            raise forms.ValidationError("Vui lòng nhập nội dung.")
        return content
