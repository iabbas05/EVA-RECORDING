# Google OAuth setup / إعداد تسجيل Google

The app uses OAuth on the Streaming PC. It does not ask for or store your Google password.

1. In Google Cloud Console, create a project and enable Gmail API and Google Drive API.
2. Configure the OAuth consent screen. If the account is a personal Gmail account, add it as a test user while the app remains in testing.
3. Create an OAuth client ID with application type **Desktop app**.
4. Download the client JSON.
5. In EVA Recording Manager, click **OAuth Setup**. Save the downloaded file in the opened `config` folder as `google_oauth_client.json`.
6. Click **Sign in**, choose the Gmail account that sends the recordings, and approve the requested Gmail send and Drive file permissions.

The token stays in Windows Credential Manager. The client JSON stays in the local app config folder. Do not send either file in chat or email.

صلاحيات التطبيق محدودة لإرسال Gmail وإنشاء ملفات Drive الخاصة بالتطبيق. يشارك الفيديو مع عناوين البريد المختارة فقط. لا يستخدم التطبيق رابطاً عاماً.
