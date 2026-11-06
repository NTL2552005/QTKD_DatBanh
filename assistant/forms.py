from django import forms

from .models import StorePolicy


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
