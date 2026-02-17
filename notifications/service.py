"""
Notification Service
Handles email and webhook notifications for alerts
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from uuid import UUID
from pathlib import Path

from config.settings import settings
from database.crud import Database, AlertCRUD, get_db_connection

logger = logging.getLogger(__name__)


class EmailService:
    """Email notification service"""
    
    def __init__(self):
        self.smtp_host = settings.email.smtp_host
        self.smtp_port = settings.email.smtp_port
        self.smtp_user = settings.email.smtp_user
        self.smtp_password = settings.email.smtp_password
        self.from_email = settings.email.from_email
        self.from_name = settings.email.from_name
    
    def send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None
    ) -> bool:
        """Send email"""
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = f"{self.from_name} <{self.from_email}>"
            msg['To'] = to_email
            
            if text_body:
                msg.attach(MIMEText(text_body, 'plain'))
            msg.attach(MIMEText(html_body, 'html'))
            
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if settings.email.smtp_tls:
                    server.starttls()
                if self.smtp_user and self.smtp_password:
                    server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.from_email, to_email, msg.as_string())
            
            logger.info(f"Email sent to {to_email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False
    
    def is_configured(self) -> bool:
        """Check if SMTP credentials are configured"""
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    LOGO_URL = "https://ipwatchai.com/static/icons/logo.jpeg"

    EMAIL_STRINGS = {
        "tr": {
            # Welcome
            "welcome_subject": "Hoş Geldiniz! — IP Watch AI",
            "welcome_heading": "Hoş Geldiniz, {name}!",
            "welcome_account_created": "Hesabınız başarıyla oluşturuldu. <strong>{plan}</strong> planındasınız.",
            "welcome_features_title": "Neler yapabilirsiniz:",
            "feature_search_title": "Marka Araştırma",
            "feature_search_desc": "Türk marka veritabanında olası çakışmaları arayın",
            "feature_watchlist_title": "İzleme Listesi",
            "feature_watchlist_desc": "Markalarınızı ekleyin, benzer markalar göründüğünde uyarı alın",
            "feature_ai_title": "Yapay Zeka Analizi",
            "feature_ai_desc": "Görsel, metin ve anlamsal benzerlik puanlama",
            "welcome_cta": "Panele Git",
            "welcome_footer": "Yardıma mı ihtiyacınız var? Bu e-postayı yanıtlayın.",
            # Email verification
            "verify_subject": "E-posta Doğrulama Kodu — IP Watch AI",
            "verify_instructions": "Hesabınızı doğrulamak için aşağıdaki kodu girin:",
            "verify_expires": "Bu kodun süresi <strong>24 saat</strong> içinde dolacaktır.",
            "verify_ignore": "Bu hesabı siz oluşturmadıysanız, bu e-postayı güvenle yok sayabilirsiniz.",
            # Password reset
            "reset_subject": "Şifre Sıfırlama Kodu — IP Watch AI",
            "reset_heading": "Şifre Sıfırlama",
            "reset_instructions": "Şifrenizi sıfırlamak için aşağıdaki kodu kullanın:",
            "reset_expires": "Bu kodun süresi <strong>15 dakika</strong> içinde dolacaktır.",
            "reset_ignore": "Bu talebi siz yapmadıysanız, bu e-postayı güvenle yok sayabilirsiniz.",
            "reset_no_change": "Şifreniz değiştirilmeyecektir.",
            # Subscription expiry
            "expiry_subject": "Abonelik Süresi Dolmak Üzere — IP Watch AI",
            "expiry_heading": "Aboneliğiniz {days} Gün İçinde Sona Eriyor",
            "expiry_body": "Merhaba {name}, <strong>{plan}</strong> planınızın süresi {days} gün içinde dolacaktır. Hizmet kesintisi yaşamamak için lütfen planınızı yenileyin.",
            "expiry_action": "Planı Yenile",
            "expiry_cta": "https://ipwatchai.com/pricing",
            "expiry_ignore": "Yenilemek istemiyorsanız, aboneliğiniz süre sonunda ücretsiz plana dönecektir.",
            # Shared
            "copyright": "&copy; 2026 IP Watch AI &mdash; ipwatchai.com",
        },
        "en": {
            "welcome_subject": "Welcome! — IP Watch AI",
            "welcome_heading": "Welcome, {name}!",
            "welcome_account_created": "Your account has been created successfully. You're on the <strong>{plan}</strong> plan.",
            "welcome_features_title": "Here's what you can do:",
            "feature_search_title": "Trademark Search",
            "feature_search_desc": "Search the Turkish trademark database for potential conflicts",
            "feature_watchlist_title": "Watchlist Monitoring",
            "feature_watchlist_desc": "Add your trademarks and get alerts when similar marks appear",
            "feature_ai_title": "AI-Powered Analysis",
            "feature_ai_desc": "Visual, textual, and semantic similarity scoring",
            "welcome_cta": "Go to Dashboard",
            "welcome_footer": "Need help? Reply to this email.",
            "verify_subject": "Email Verification Code — IP Watch AI",
            "verify_instructions": "Enter the code below to verify your account:",
            "verify_expires": "This code expires in <strong>24 hours</strong>.",
            "verify_ignore": "If you didn't create this account, you can safely ignore this email.",
            "reset_subject": "Password Reset Code — IP Watch AI",
            "reset_heading": "Password Reset",
            "reset_instructions": "Use the code below to reset your password:",
            "reset_expires": "This code expires in <strong>15 minutes</strong>.",
            "reset_ignore": "If you didn't request a password reset, you can safely ignore this email.",
            "reset_no_change": "Your password will not be changed.",
            "expiry_subject": "Subscription Expiring Soon — IP Watch AI",
            "expiry_heading": "Your Subscription Expires in {days} Days",
            "expiry_body": "Hi {name}, your <strong>{plan}</strong> plan expires in {days} days. Please renew to avoid service interruption.",
            "expiry_action": "Renew Plan",
            "expiry_cta": "https://ipwatchai.com/pricing",
            "expiry_ignore": "If you don't wish to renew, your subscription will revert to the free plan at the end of the period.",
            "copyright": "&copy; 2026 IP Watch AI &mdash; ipwatchai.com",
        },
        "ar": {
            "welcome_subject": "!مرحبًا بك — IP Watch AI",
            "welcome_heading": "!مرحبًا، {name}",
            "welcome_account_created": "تم إنشاء حسابك بنجاح. أنت على خطة <strong>{plan}</strong>.",
            "welcome_features_title": "إليك ما يمكنك فعله:",
            "feature_search_title": "بحث العلامات التجارية",
            "feature_search_desc": "ابحث في قاعدة بيانات العلامات التجارية التركية عن التعارضات المحتملة",
            "feature_watchlist_title": "قائمة المراقبة",
            "feature_watchlist_desc": "أضف علاماتك التجارية واحصل على تنبيهات عند ظهور علامات مشابهة",
            "feature_ai_title": "تحليل بالذكاء الاصطناعي",
            "feature_ai_desc": "تقييم التشابه البصري والنصي والدلالي",
            "welcome_cta": "الذهاب إلى لوحة التحكم",
            "welcome_footer": "هل تحتاج مساعدة؟ قم بالرد على هذا البريد الإلكتروني.",
            "verify_subject": "رمز التحقق من البريد الإلكتروني — IP Watch AI",
            "verify_instructions": "أدخل الرمز أدناه للتحقق من حسابك:",
            "verify_expires": "ينتهي هذا الرمز خلال <strong>24 ساعة</strong>.",
            "verify_ignore": "إذا لم تقم بإنشاء هذا الحساب، يمكنك تجاهل هذا البريد بأمان.",
            "reset_subject": "رمز إعادة تعيين كلمة المرور — IP Watch AI",
            "reset_heading": "إعادة تعيين كلمة المرور",
            "reset_instructions": "استخدم الرمز أدناه لإعادة تعيين كلمة المرور:",
            "reset_expires": "ينتهي هذا الرمز خلال <strong>15 دقيقة</strong>.",
            "reset_ignore": "إذا لم تطلب إعادة تعيين كلمة المرور، يمكنك تجاهل هذا البريد بأمان.",
            "reset_no_change": "لن يتم تغيير كلمة المرور الخاصة بك.",
            "expiry_subject": "اشتراكك على وشك الانتهاء — IP Watch AI",
            "expiry_heading": "ينتهي اشتراكك خلال {days} أيام",
            "expiry_body": "مرحبًا {name}، ستنتهي صلاحية خطة <strong>{plan}</strong> الخاصة بك خلال {days} أيام. يرجى التجديد لتجنب انقطاع الخدمة.",
            "expiry_action": "تجديد الخطة",
            "expiry_cta": "https://ipwatchai.com/pricing",
            "expiry_ignore": "إذا كنت لا ترغب في التجديد، فسيعود اشتراكك إلى الخطة المجانية في نهاية الفترة.",
            "copyright": "&copy; 2026 IP Watch AI &mdash; ipwatchai.com",
        },
    }

    def _get_strings(self, lang: str) -> dict:
        """Get email strings for a language, fallback to Turkish"""
        return self.EMAIL_STRINGS.get(lang, self.EMAIL_STRINGS["tr"])

    def _logo_header_html(self) -> str:
        """Shared logo header block for all emails"""
        return f"""
                <div style="text-align: center; margin-bottom: 30px;">
                    <img src="{self.LOGO_URL}" alt="IP Watch AI"
                         style="width: 120px; height: 120px; border-radius: 16px; object-fit: cover;"
                    />
                </div>"""

    def _email_direction(self, lang: str) -> str:
        """Return 'rtl' for Arabic, 'ltr' otherwise"""
        return "rtl" if lang == "ar" else "ltr"

    def send_welcome(self, to_email: str, first_name: str, plan_name: str = "Free", lang: str = "tr", verification_code: Optional[str] = None) -> bool:
        """Send welcome email to newly registered user, optionally with verification code"""
        s = self._get_strings(lang)
        direction = self._email_direction(lang)
        display_name = first_name or "User"
        subject = s["verify_subject"] if verification_code else s["welcome_subject"]

        # Verification code block (inserted between welcome text and features)
        verification_block = ""
        if verification_code:
            verification_block = f"""
                <p style="color: #555; text-align: center; font-size: 15px; margin-top: 15px;">{s["verify_instructions"]}</p>
                <div style="background: #f0f4ff; border: 2px dashed #1a73e8; border-radius: 8px; padding: 20px; text-align: center; margin: 20px 0;">
                    <span style="font-size: 36px; font-weight: bold; letter-spacing: 8px; color: #1a73e8;">{verification_code}</span>
                </div>
                <p style="color: #888; text-align: center; font-size: 14px;">{s["verify_expires"]}</p>
            """

        verification_footer = ""
        if verification_code:
            verification_footer = f"""
                <p style="color: #999; font-size: 12px; text-align: center; margin-top: 10px;">
                    {s["verify_ignore"]}
                </p>
            """

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #f4f6f9; padding: 20px; direction: {direction};">
            <div style="background: white; border-radius: 8px; padding: 40px; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
                {self._logo_header_html()}

                <h2 style="color: #333; text-align: center; margin-bottom: 10px;">{s["welcome_heading"].format(name=display_name)}</h2>
                <p style="color: #555; text-align: center; font-size: 16px;">
                    {s["welcome_account_created"].format(plan=plan_name)}
                </p>

                {verification_block}

                <div style="background: #f0f4ff; border-radius: 8px; padding: 25px; margin: 25px 0;">
                    <h3 style="color: #1a73e8; margin-top: 0;">{s["welcome_features_title"]}</h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr>
                            <td style="padding: 8px 10px; vertical-align: top; width: 30px; font-size: 18px;">&#128269;</td>
                            <td style="padding: 8px 10px;">
                                <strong style="color: #333;">{s["feature_search_title"]}</strong><br>
                                <span style="color: #666; font-size: 14px;">{s["feature_search_desc"]}</span>
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 10px; vertical-align: top; width: 30px; font-size: 18px;">&#128065;</td>
                            <td style="padding: 8px 10px;">
                                <strong style="color: #333;">{s["feature_watchlist_title"]}</strong><br>
                                <span style="color: #666; font-size: 14px;">{s["feature_watchlist_desc"]}</span>
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 10px; vertical-align: top; width: 30px; font-size: 18px;">&#129302;</td>
                            <td style="padding: 8px 10px;">
                                <strong style="color: #333;">{s["feature_ai_title"]}</strong><br>
                                <span style="color: #666; font-size: 14px;">{s["feature_ai_desc"]}</span>
                            </td>
                        </tr>
                    </table>
                </div>

                <div style="text-align: center; margin: 30px 0;">
                    <a href="https://ipwatchai.com/dashboard"
                       style="background: #1a73e8; color: white; padding: 14px 32px;
                              text-decoration: none; border-radius: 6px; font-size: 16px;
                              font-weight: bold; display: inline-block;">
                        {s["welcome_cta"]}
                    </a>
                </div>

                <hr style="border: none; border-top: 1px solid #eee; margin: 25px 0;">
                {verification_footer}
                <p style="color: #999; font-size: 12px; text-align: center;">
                    {s["welcome_footer"]}<br>
                    {s["copyright"]}
                </p>
            </div>
        </body>
        </html>
        """

        text_body = f"""{s["welcome_heading"].format(name=display_name)}

{s["welcome_account_created"].format(plan=plan_name)}
"""
        if verification_code:
            text_body += f"\n{s['verify_instructions']} {verification_code}\n"

        text_body += f"""
{s["feature_search_title"]}: {s["feature_search_desc"]}
{s["feature_watchlist_title"]}: {s["feature_watchlist_desc"]}
{s["feature_ai_title"]}: {s["feature_ai_desc"]}

https://ipwatchai.com/dashboard
"""

        return self.send_email(to_email, subject, html_body, text_body)

    def send_password_reset(self, to_email: str, code: str, lang: str = "tr") -> bool:
        """Send password reset email with 6-digit code"""
        s = self._get_strings(lang)
        direction = self._email_direction(lang)
        subject = s["reset_subject"]

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #f4f6f9; padding: 20px; direction: {direction};">
            <div style="background: white; border-radius: 8px; padding: 40px; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
                {self._logo_header_html()}
                <h2 style="color: #333; text-align: center; margin-bottom: 10px;">{s["reset_heading"]}</h2>
                <p style="color: #555; text-align: center;">{s["reset_instructions"]}</p>
                <div style="background: #f0f4ff; border: 2px dashed #1a73e8; border-radius: 8px; padding: 20px; text-align: center; margin: 25px 0;">
                    <span style="font-size: 36px; font-weight: bold; letter-spacing: 8px; color: #1a73e8;">{code}</span>
                </div>
                <p style="color: #888; text-align: center; font-size: 14px;">{s["reset_expires"]}</p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 25px 0;">
                <p style="color: #999; font-size: 12px; text-align: center;">
                    {s["reset_ignore"]}<br>{s["reset_no_change"]}
                </p>
                <p style="color: #bbb; font-size: 11px; text-align: center;">{s["copyright"]}</p>
            </div>
        </body>
        </html>
        """

        text_body = f"""{s["reset_heading"]} — IP Watch AI

{s["reset_instructions"]} {code}

{s["reset_ignore"]}"""

        return self.send_email(to_email, subject, html_body, text_body)

    def send_subscription_expiry_reminder(
        self,
        to_email: str,
        first_name: str,
        plan_name: str,
        days_remaining: int,
        renewal_url: str = "https://ipwatchai.com/pricing",
        lang: str = "tr",
    ) -> bool:
        """Send subscription expiry reminder email (7d, 3d, 1d before)."""
        s = self._get_strings(lang)
        direction = self._email_direction(lang)
        display_name = first_name or "User"
        subject = s["expiry_subject"]

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #f4f6f9; padding: 20px; direction: {direction};">
            <div style="background: white; border-radius: 8px; padding: 40px; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
                {self._logo_header_html()}

                <h2 style="color: #e67e22; text-align: center; margin-bottom: 10px;">
                    {s["expiry_heading"].format(days=days_remaining)}
                </h2>
                <p style="color: #555; text-align: center; font-size: 16px;">
                    {s["expiry_body"].format(name=display_name, plan=plan_name, days=days_remaining)}
                </p>

                <div style="text-align: center; margin: 30px 0;">
                    <a href="{renewal_url}"
                       style="background: #e67e22; color: white; padding: 14px 32px;
                              text-decoration: none; border-radius: 6px; font-size: 16px;
                              font-weight: bold; display: inline-block;">
                        {s["expiry_action"]}
                    </a>
                </div>

                <hr style="border: none; border-top: 1px solid #eee; margin: 25px 0;">
                <p style="color: #999; font-size: 12px; text-align: center;">
                    {s["expiry_ignore"]}<br>
                    {s["copyright"]}
                </p>
            </div>
        </body>
        </html>
        """

        text_body = f"""{s["expiry_heading"].format(days=days_remaining)}

{s["expiry_body"].format(name=display_name, plan=plan_name, days=days_remaining)}

{renewal_url}

{s["expiry_ignore"]}"""

        return self.send_email(to_email, subject, html_body, text_body)

    def send_alert_notification(
        self,
        to_email: str,
        user_name: str,
        alert: Dict,
        watchlist_item: Dict
    ) -> bool:
        """Send immediate alert notification"""
        subject = f"⚠️ Trademark Alert: {alert['conflicting_name']} conflicts with {watchlist_item['brand_name']}"
        
        severity_emoji = {
            'critical': '🔴',
            'high': '🟠',
            'medium': '🟡',
            'low': '🟢'
        }
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: #f8f9fa; padding: 20px; border-radius: 8px;">
                <h2 style="color: #333;">Trademark Conflict Detected</h2>
                
                <p>Hi {user_name},</p>
                
                <p>We've detected a potential conflict with one of your monitored trademarks:</p>
                
                <div style="background: white; padding: 15px; border-radius: 4px; border-left: 4px solid 
                    {'#dc3545' if alert['severity'] == 'critical' else '#ffc107'};">
                    
                    <p><strong>Your Trademark:</strong> {watchlist_item['brand_name']}</p>
                    <p><strong>Conflicting Trademark:</strong> {alert['conflicting_brand_name']}</p>
                    <p><strong>Application No:</strong> {alert['conflicting_application_no']}</p>
                    <p><strong>Classes:</strong> {', '.join(map(str, alert.get('conflicting_nice_classes', [])))}</p>

                    <hr style="border: none; border-top: 1px solid #eee; margin: 15px 0;">

                    <p><strong>Similarity Score:</strong> {alert['overall_risk_score']:.0%}</p>
                    <p><strong>Severity:</strong> {severity_emoji.get(alert['severity'], '⚪')} {alert['severity'].upper()}</p>
                </div>
                
                <p style="margin-top: 20px;">
                    <a href="{settings.app_name}/alerts/{alert['id']}" 
                       style="background: #007bff; color: white; padding: 10px 20px; 
                              text-decoration: none; border-radius: 4px;">
                        View Alert Details
                    </a>
                </p>
                
                <p style="color: #666; font-size: 12px; margin-top: 30px;">
                    You're receiving this because you have alerts enabled for "{watchlist_item['brand_name']}".
                    <br>
                    To change your notification settings, visit your watchlist settings.
                </p>
            </div>
        </body>
        </html>
        """
        
        text_body = f"""
Trademark Conflict Detected

Hi {user_name},

We've detected a potential conflict:

Your Trademark: {watchlist_item['brand_name']}
Conflicting: {alert['conflicting_brand_name']} ({alert['conflicting_application_no']})
Similarity: {alert['overall_risk_score']:.0%}
Severity: {alert['severity'].upper()}

View details at: {settings.app_name}/alerts/{alert['id']}
        """
        
        return self.send_email(to_email, subject, html_body, text_body)
    
    def send_daily_digest(
        self,
        to_email: str,
        user_name: str,
        alerts: List[Dict],
        period: str = "daily"
    ) -> bool:
        """Send daily/weekly digest of alerts"""
        if not alerts:
            return True  # Nothing to send
        
        critical_count = sum(1 for a in alerts if a['severity'] == 'critical')
        high_count = sum(1 for a in alerts if a['severity'] == 'high')
        
        subject = f"📊 Trademark Alert Digest: {len(alerts)} new alert(s)"
        if critical_count:
            subject = f"🔴 {critical_count} Critical Alert(s) - {subject}"
        
        # Build alerts table
        alerts_html = ""
        for alert in alerts[:20]:  # Limit to 20 in email
            severity_color = {
                'critical': '#dc3545',
                'high': '#fd7e14',
                'medium': '#ffc107',
                'low': '#28a745'
            }.get(alert['severity'], '#6c757d')
            
            alerts_html += f"""
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #eee;">
                    {alert.get('watched_brand_name', 'N/A')}
                </td>
                <td style="padding: 10px; border-bottom: 1px solid #eee;">
                    {alert['conflicting_name']}
                </td>
                <td style="padding: 10px; border-bottom: 1px solid #eee;">
                    {alert['similarity_score']:.0%}
                </td>
                <td style="padding: 10px; border-bottom: 1px solid #eee;">
                    <span style="background: {severity_color}; color: white; 
                                 padding: 2px 8px; border-radius: 4px; font-size: 12px;">
                        {alert['severity'].upper()}
                    </span>
                </td>
            </tr>
            """
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto;">
            <div style="background: #f8f9fa; padding: 20px; border-radius: 8px;">
                <h2 style="color: #333;">Your {period.title()} Alert Digest</h2>
                
                <p>Hi {user_name},</p>
                
                <p>Here's a summary of trademark alerts from the past {'day' if period == 'daily' else 'week'}:</p>
                
                <div style="background: white; padding: 15px; border-radius: 4px; margin: 20px 0;">
                    <h3 style="margin-top: 0;">Summary</h3>
                    <p>
                        <strong>Total Alerts:</strong> {len(alerts)}<br>
                        <strong>Critical:</strong> {critical_count}<br>
                        <strong>High:</strong> {high_count}
                    </p>
                </div>
                
                <table style="width: 100%; border-collapse: collapse; background: white;">
                    <thead>
                        <tr style="background: #f1f1f1;">
                            <th style="padding: 10px; text-align: left;">Your Brand</th>
                            <th style="padding: 10px; text-align: left;">Conflict</th>
                            <th style="padding: 10px; text-align: left;">Score</th>
                            <th style="padding: 10px; text-align: left;">Severity</th>
                        </tr>
                    </thead>
                    <tbody>
                        {alerts_html}
                    </tbody>
                </table>
                
                {f'<p style="color: #666;">Showing first 20 of {len(alerts)} alerts.</p>' if len(alerts) > 20 else ''}
                
                <p style="margin-top: 20px;">
                    <a href="{settings.app_name}/alerts" 
                       style="background: #007bff; color: white; padding: 10px 20px; 
                              text-decoration: none; border-radius: 4px;">
                        View All Alerts
                    </a>
                </p>
            </div>
        </body>
        </html>
        """
        
        return self.send_email(to_email, subject, html_body)


class WebhookService:
    """Webhook notification service"""
    
    @staticmethod
    def send_webhook(
        url: str,
        payload: Dict,
        headers: Optional[Dict] = None
    ) -> bool:
        """Send webhook notification"""
        try:
            default_headers = {
                'Content-Type': 'application/json',
                'User-Agent': 'TrademarkRiskSystem/1.0'
            }
            if headers:
                default_headers.update(headers)
            
            response = requests.post(
                url,
                json=payload,
                headers=default_headers,
                timeout=30
            )
            
            if response.status_code >= 200 and response.status_code < 300:
                logger.info(f"Webhook sent successfully to {url}")
                return True
            else:
                logger.error(f"Webhook failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return False
    
    @staticmethod
    def send_alert_webhook(
        url: str,
        alert: Dict,
        watchlist_item: Dict
    ) -> bool:
        """Send alert via webhook"""
        payload = {
            "event": "trademark.alert.new",
            "timestamp": datetime.utcnow().isoformat(),
            "data": {
                "alert_id": str(alert['id']),
                "severity": alert['severity'],
                "similarity_score": alert['overall_risk_score'],
                "watched_trademark": {
                    "name": watchlist_item['brand_name'],
                    "classes": watchlist_item.get('nice_classes', [])
                },
                "conflicting_trademark": {
                    "name": alert['conflicting_brand_name'],
                    "application_no": alert['conflicting_application_no'],
                    "classes": alert.get('conflicting_nice_classes', []),
                    "holder": alert.get('conflicting_holder_name')
                },
                "scores": {
                    "text_similarity": alert.get('text_similarity'),
                    "semantic_similarity": alert.get('semantic_similarity'),
                    "visual_similarity": alert.get('visual_similarity')
                }
            }
        }
        
        return WebhookService.send_webhook(url, payload)


class NotificationWorker:
    """
    Background worker for processing notifications.
    Run with: python -m notifications.service worker
    """
    
    def __init__(self):
        self.email_service = EmailService()
        self.db = Database(get_db_connection())
    
    def process_immediate_notifications(self):
        """Process pending immediate notifications"""
        logger.info("Processing immediate notifications...")
        
        # Get pending alerts for immediate notification
        alerts = AlertCRUD.get_pending_notifications(self.db, 'email', 'immediate')
        
        for alert in alerts:
            try:
                # Send email
                if alert.get('alert_email'):
                    success = self.email_service.send_alert_notification(
                        to_email=alert['user_email'],
                        user_name=alert.get('first_name', 'User'),
                        alert=alert,
                        watchlist_item={'brand_name': alert['brand_name']}
                    )
                    
                    if success:
                        AlertCRUD.mark_notified(self.db, UUID(alert['id']), 'email')
                
                # Send webhook
                # (Would implement similarly)
                
            except Exception as e:
                logger.error(f"Failed to process notification for alert {alert['id']}: {e}")
        
        logger.info(f"Processed {len(alerts)} immediate notifications")
    
    def process_daily_digest(self):
        """Process daily digest notifications"""
        logger.info("Processing daily digest...")
        
        # Get alerts from last 24 hours grouped by user
        cur = self.db.cursor()
        cur.execute("""
            SELECT
                u.id as user_id,
                u.email,
                u.first_name,
                u.organization_id,
                array_agg(a.id) as alert_ids
            FROM alerts_mt a
            JOIN watchlist_mt w ON a.watchlist_item_id = w.id
            JOIN users u ON w.user_id = u.id
            WHERE a.created_at > NOW() - INTERVAL '24 hours'
              AND a.email_sent = FALSE
              AND w.alert_frequency = 'daily'
              AND w.alert_email = TRUE
            GROUP BY u.id, u.email, u.first_name, u.organization_id
        """)

        for row in cur.fetchall():
            try:
                # Get full alert details
                alerts = []
                for alert_id in row['alert_ids']:
                    alert = AlertCRUD.get_by_id(self.db, UUID(alert_id), UUID(str(row['organization_id'])))
                    if alert:
                        alerts.append(alert)
                
                if alerts:
                    success = self.email_service.send_daily_digest(
                        to_email=row['email'],
                        user_name=row['first_name'] or 'User',
                        alerts=alerts,
                        period='daily'
                    )
                    
                    if success:
                        for alert_id in row['alert_ids']:
                            AlertCRUD.mark_notified(self.db, UUID(alert_id), 'email')
                            
            except Exception as e:
                logger.error(f"Failed to send daily digest to {row['email']}: {e}")
        
        logger.info("Daily digest processing complete")
    
    def process_weekly_digest(self):
        """Process weekly digest notifications"""
        logger.info("Processing weekly digest...")
        
        cur = self.db.cursor()
        cur.execute("""
            SELECT
                u.id as user_id,
                u.email,
                u.first_name,
                array_agg(a.id) as alert_ids
            FROM alerts_mt a
            JOIN watchlist_mt w ON a.watchlist_item_id = w.id
            JOIN users u ON w.user_id = u.id
            WHERE a.created_at > NOW() - INTERVAL '7 days'
              AND a.email_sent = FALSE
              AND w.notification_frequency = 'weekly'
              AND w.notify_email = TRUE
            GROUP BY u.id, u.email, u.first_name
        """)
        
        for row in cur.fetchall():
            try:
                alerts = []
                for alert_id in row['alert_ids']:
                    alert = AlertCRUD.get_by_id(self.db, UUID(alert_id))
                    if alert:
                        alerts.append(alert)
                
                if alerts:
                    success = self.email_service.send_daily_digest(
                        to_email=row['email'],
                        user_name=row['first_name'] or 'User',
                        alerts=alerts,
                        period='weekly'
                    )
                    
                    if success:
                        for alert_id in row['alert_ids']:
                            AlertCRUD.mark_notified(self.db, UUID(alert_id), 'email')
                            
            except Exception as e:
                logger.error(f"Failed to send weekly digest to {row['email']}: {e}")
        
        logger.info("Weekly digest processing complete")
    
    def run(self):
        """Run notification worker (called by scheduler)"""
        import schedule
        import time
        
        # Immediate notifications every minute
        schedule.every(1).minutes.do(self.process_immediate_notifications)
        
        # Daily digest at 9 AM
        schedule.every().day.at("09:00").do(self.process_daily_digest)
        
        # Weekly digest on Monday at 9 AM
        schedule.every().monday.at("09:00").do(self.process_weekly_digest)
        
        logger.info("Notification worker started")
        
        while True:
            schedule.run_pending()
            time.sleep(30)


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker = NotificationWorker()
        worker.run()
    else:
        print("Usage: python -m notifications.service worker")
