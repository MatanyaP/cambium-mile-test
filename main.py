# main.py
import json
import base64
import logging
import streamlit as st
from prompt_utils import load_article_contexts, get_instructions_template, display_article_and_keywords
from st_utils import get_logger

st.set_page_config(
    page_title="Cambium Mile",
    layout="wide",
)

# Configure logger
logger = get_logger(__name__)

def check_password():
    """Returns `True` if the user had the correct password."""
    if "password_correct" not in st.session_state:
        st.text_input("Password", type="password", key="password", on_change=password_entered)
        return False
    return st.session_state["password_correct"]

def password_entered():
    """Checks whether a password entered by the user is correct."""
    if st.session_state["password"] == st.secrets["password"]:
        st.session_state["password_correct"] = True
        del st.session_state["password"]
    else:
        st.session_state["password_correct"] = False

def set_page_layout():
    """Set up the page layout with custom styles and logos"""

    st.markdown("""
        <style>
            .header-container {
                display: flex;
                justify-content: center;
                padding: 1rem 0;
                margin-bottom: 2rem;
            }

            .header-logo {
                max-width: 200px;
                height: auto;
            }

            .footer-container {
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                background-color: rgba(255, 255, 255, 0.9);
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 0.3rem 0;
                box-shadow: 0 -1px 3px rgba(0,0,0,0.05);
                z-index: 1000;
            }

            .footer-logo {
                width: 100px !important;
                height: auto !important;
                opacity: 0.7;
                max-width: none;
            }

            /* Add hover effect */
            .footer-logo:hover {
                opacity: 1;
                transition: opacity 0.3s ease;
            }

            /* Add padding to main content to prevent overlap with footer */
            .main-content {
                padding-bottom: 100px;
            }
        </style>
    """, unsafe_allow_html=True)
# def set_page_layout():
#     """Set up the page layout with custom styles and logos"""
#
#     st.markdown("""
#         <style>
#             .header-container {
#                 display: flex;
#                 justify-content: center;
#                 padding: 1rem 0;
#                 margin-bottom: 2rem;
#             }
#
#             .header-logo {
#                 max-width: 200px;
#                 height: auto;
#             }
#
#             .footer-container {
#                 position: fixed;
#                 bottom: 0;
#                 left: 0;
#                 right: 0;
#                 background-color: rgba(255, 255, 255, 0.9);
#                 display: flex;
#                 justify-content: center;
#                 padding: 0.5rem 0;  /* Reduced padding */
#                 box-shadow: 0 -1px 3px rgba(0,0,0,0.05);  /* Subtler shadow */
#             }
#
#             .footer-logo {
#                 max-width: 10px;  /* Smaller logo */
#                 height: auto;
#                 opacity: 0.7;  /* Slightly transparent */
#             }
#
#             /* Add hover effect */
#             .footer-logo:hover {
#                 opacity: 1;
#                 transition: opacity 0.3s ease;
#             }
#
#             /* Add padding to main content to prevent overlap with footer */
#             .main-content {
#                 padding-bottom: 100px;  /* Reduced padding to match smaller footer */
#             }
#         </style>
#     """, unsafe_allow_html=True)


def get_js_code():
    """Return the JavaScript code as a string"""
    return """
        document.addEventListener('DOMContentLoaded', function() {
            console.log("Script loaded");

            // Add debug logging for audio context
            navigator.mediaDevices.getUserMedia({ audio: true })
                .then(() => console.log("Microphone permission granted"))
                .catch(err => console.error("Microphone error:", err));

            const startButton = document.getElementById('startButton');
            const stopButton = document.getElementById('stopButton');
            const statusDiv = document.getElementById('status');
            const errorDiv = document.getElementById('error');

            let peerConnection = null;
            let audioStream = null;
            let dataChannel = null;

            const INITIAL_INSTRUCTIONS = INSTRUCTIONS_PLACEHOLDER;
            const API_KEY = API_KEY_PLACEHOLDER;

            // Add event listeners
            startButton.addEventListener('click', init);
            stopButton.addEventListener('click', stopRecording);

            async function init() {
                startButton.disabled = true;
                try {
                    updateStatus('Initializing...');

                    // Connect directly to OpenAI's API
                    peerConnection = new RTCPeerConnection();
                    await setupAudio();
                    setupDataChannel();

                    const offer = await peerConnection.createOffer();
                    await peerConnection.setLocalDescription(offer);

                    const sdpResponse = await fetch(`https://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01`, {
                        method: "POST",
                        body: offer.sdp,
                        headers: {
                            Authorization: `Bearer ${API_KEY}`,
                            "Content-Type": "application/sdp",
                            "OpenAI-Beta": "realtime=v1"
                        },
                    });

                    if (!sdpResponse.ok) {
                        throw new Error(`OpenAI API error: ${sdpResponse.status}`);
                    }

                    const answer = {
                        type: "answer",
                        sdp: await sdpResponse.text(),
                    };
                    await peerConnection.setRemoteDescription(answer);

                    updateStatus('Connected');
                    stopButton.disabled = false;
                    hideError();

                } catch (error) {
                    startButton.disabled = false;
                    stopButton.disabled = true;
                    showError('Error: ' + error.message);
                    console.error('Initialization error:', error);
                    updateStatus('Failed to connect');
                }
            }

            async function setupAudio() {
                try {
                    // Create and configure audio element for playback
                    const audioEl = document.createElement("audio");
                    audioEl.autoplay = true;
                    document.body.appendChild(audioEl); // Add to DOM

                    // Set up audio stream from microphone
                    audioStream = await navigator.mediaDevices.getUserMedia({
                        audio: {
                            echoCancellation: true,
                            noiseSuppression: true,
                            sampleRate: 48000,
                            channelCount: 1
                        }
                    });

                    // Handle incoming audio
                    peerConnection.ontrack = (event) => {
                        console.log("Received audio track");
                        audioEl.srcObject = event.streams[0];
                    };

                    // Add microphone audio to peer connection
                    audioStream.getTracks().forEach(track => {
                        peerConnection.addTrack(track, audioStream);
                    });

                    console.log("Audio setup completed");
                } catch (error) {
                    console.error("Error setting up audio:", error);
                    throw error;
                }
            }

            function setupDataChannel() {
                dataChannel = peerConnection.createDataChannel("oai-events");
                dataChannel.onopen = onDataChannelOpen;
                dataChannel.onmessage = handleMessage;
                dataChannel.onerror = (error) => {
                    console.error("DataChannel error:", error);
                    showError("DataChannel error: " + error.message);
                };
                console.log("DataChannel setup completed");
            }

            function handleMessage(event) {
                try {
                    const message = JSON.parse(event.data);
                    console.log('Received message:', message);

                    switch (message.type) {
                        case "response.done":
                            handleTranscript(message);
                            break;
                        case "response.audio.delta":
                            handleAudioDelta(message);
                            break;
                        case "input_audio_buffer.speech_started":
                            console.log("Speech started");
                            createUserMessageContainer();
                            break;
                        case "input_audio_buffer.speech_ended":
                            console.log("Speech ended");
                            break;
                        case "conversation.item.input_audio_transcription.completed":
                            handleUserTranscript(message);
                            break;
                        case "error":
                            console.error("Error from API:", message.error);
                            showError(message.error.message);
                            break;
                        default:
                            console.log('Message type:', message.type);
                    }
                } catch (error) {
                    console.error('Error processing message:', error);
                    showError('Error processing message: ' + error.message);
                }
            }

            let currentUserMessage = null;

            function createUserMessageContainer() {
                const chatContainer = document.getElementById('chat-container');
                currentUserMessage = document.createElement('div');
                currentUserMessage.className = 'message user-message';

                const label = document.createElement('div');
                label.className = 'message-label';
                label.textContent = 'You';

                const content = document.createElement('div');
                content.className = 'message-content';

                currentUserMessage.appendChild(label);
                currentUserMessage.appendChild(content);
                chatContainer.appendChild(currentUserMessage);
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }

            function handleUserTranscript(message) {
                if (currentUserMessage && message.transcript) {
                    const content = currentUserMessage.querySelector('.message-content');
                    // If there's existing content, append a space before the new transcript
                    if (content.textContent) {
                        content.textContent = content.textContent + " " + message.transcript;
                    } else {
                        content.textContent = message.transcript;
                    }
                    const chatContainer = document.getElementById('chat-container');
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                }
            }

            function handleAudioDelta(message) {
                if (message.delta) {
                    console.log("Received audio data");
                }
            }

            function handleTranscript(message) {
                const chatContainer = document.getElementById('chat-container');

                if (message.response?.output?.[0]?.content?.[0]?.transcript) {
                    const transcript = message.response.output[0].content[0].transcript;

                    const botMessage = document.createElement('div');
                    botMessage.className = 'message bot-message';

                    const label = document.createElement('div');
                    label.className = 'message-label';
                    label.textContent = 'Assistant';

                    const content = document.createElement('div');
                    content.className = 'message-content';
                    content.textContent = transcript;

                    botMessage.appendChild(label);
                    botMessage.appendChild(content);
                    chatContainer.appendChild(botMessage);
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                }
            }

            function sendSessionUpdate() {
                const sessionUpdateEvent = {
                    "type": "session.update",
                    "session": {
                        "instructions": INITIAL_INSTRUCTIONS,
                        "modalities": ["text", "audio"],
                        "voice": "alloy",
                        "input_audio_format": "pcm16",
                        "output_audio_format": "pcm16",
                        "input_audio_transcription": {
                            "model": "whisper-1",
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 800,
                        }
                    }
                };
                sendMessage(sessionUpdateEvent);
            }

            function sendMessage(message) {
                if (dataChannel?.readyState === "open") {
                    dataChannel.send(JSON.stringify(message));
                    console.log('Sent message:', message);
                }
            }

            function onDataChannelOpen() {
                sendSessionUpdate();
                sendResponseCreate();
            }

            function sendResponseCreate() {
                sendMessage({ "type": "response.create" });
            }

            function stopRecording() {
                if (peerConnection) {
                    peerConnection.close();
                    peerConnection = null;
                }
                if (audioStream) {
                    audioStream.getTracks().forEach(track => track.stop());
                    audioStream = null;
                }
                if (dataChannel) {
                    dataChannel.close();
                    dataChannel = null;
                }
                startButton.disabled = false;
                stopButton.disabled = true;
                updateStatus('Ready to start');
            }

            function updateStatus(message) {
                statusDiv.textContent = message;
            }

            function showError(message) {
                errorDiv.style.display = 'block';
                errorDiv.textContent = message;
            }

            function hideError() {
                errorDiv.style.display = 'none';
            }
        });
    """

def get_webrtc_html(article_data):
    """Generate the HTML for WebRTC interface"""
    instructions = json.dumps(get_instructions_template(article_data['content']))
    api_key = json.dumps(st.secrets["OPENAI_API_KEY"])

    js_code = get_js_code()
    js_code = js_code.replace('INSTRUCTIONS_PLACEHOLDER', instructions)
    js_code = js_code.replace('API_KEY_PLACEHOLDER', api_key)

    html_template = '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Voice Chat</title>
        <style>
            :root {
                --primary-color: #2563eb;
                --primary-hover: #1d4ed8;
                --bg-light: #f8fafc;
                --border-color: #e2e8f0;
                --text-primary: #1e293b;
                --text-secondary: #64748b;
                --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
                --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            }

            .container {
                max-width: 100%;
                margin: 0 auto;
                padding: 12px;
                font-family: system-ui, -apple-system, sans-serif;
                color: var(--text-primary);
            }

            .controls {
                text-align: center;
                margin: 24px 0;
                display: flex;
                gap: 12px;
                justify-content: center;
            }

            .chat-container {
                margin: 24px 0;
                padding: 20px;
                border: 1px solid var(--border-color);
                border-radius: 12px;
                min-height: 300px;
                max-height: 600px;
                overflow-y: auto;
                background-color: var(--bg-light);
                box-shadow: var(--shadow-sm);
            }

            .message {
                margin: 16px 0;
                padding: 12px 16px;
                border-radius: 12px;
                max-width: 85%;
                box-shadow: var(--shadow-sm);
                line-height: 1.5;
                position: relative;
                animation: fadeIn 0.3s ease-in-out;
            }

            @keyframes fadeIn {
                from { opacity: 0; transform: translateY(10px); }
                to { opacity: 1; transform: translateY(0); }
            }

            .user-message {
                background-color: var(--primary-color);
                color: white;
                margin-left: auto;
                margin-right: 16px;
            }

            .bot-message {
                background-color: white;
                margin-left: 16px;
                margin-right: auto;
                border: 1px solid var(--border-color);
            }

            .message-label {
                font-size: 0.75rem;
                color: var(--text-secondary);
                margin-bottom: 4px;
                font-weight: 500;
                text-transform: uppercase;
                letter-spacing: 0.05em;
            }

            .user-message .message-label {
                color: rgba(255, 255, 255, 0.9);
            }

            .status {
                text-align: center;
                margin: 12px 0;
                padding: 8px;
                color: var(--text-secondary);
                font-size: 0.875rem;
                border-radius: 8px;
                background-color: var(--bg-light);
            }

            .error {
                color: #dc2626;
                display: none;
                margin: 12px 0;
                padding: 12px;
                border-radius: 8px;
                background-color: #fef2f2;
                border: 1px solid #fee2e2;
                font-size: 0.875rem;
            }

            button {
                padding: 12px 24px;
                border-radius: 8px;
                border: none;
                background-color: var(--primary-color);
                color: white;
                cursor: pointer;
                font-weight: 500;
                font-size: 0.875rem;
                transition: all 0.2s ease;
                min-width: 140px;
            }

            button:hover:not(:disabled) {
                background-color: var(--primary-hover);
                transform: translateY(-1px);
                box-shadow: var(--shadow-md);
            }

            button:disabled {
                background-color: var(--text-secondary);
                cursor: not-allowed;
                opacity: 0.7;
            }

            /* Custom scrollbar for modern browsers */
            .chat-container::-webkit-scrollbar {
                width: 8px;
            }

            .chat-container::-webkit-scrollbar-track {
                background: var(--bg-light);
                border-radius: 4px;
            }

            .chat-container::-webkit-scrollbar-thumb {
                background: var(--text-secondary);
                border-radius: 4px;
            }

            .chat-container::-webkit-scrollbar-thumb:hover {
                background: var(--text-primary);
            }

            /* Responsive adjustments */
            @media (max-width: 640px) {
                .container {
                    padding: 16px;
                }

                .message {
                    max-width: 90%;
                }

                button {
                    padding: 10px 20px;
                    min-width: 120px;
                }
            }
        </style>

    </head>
    <body>
        <div class="container">
            <div class="controls">
                <button id="startButton">Start Conversation</button>
                <button id="stopButton" disabled>End Conversation</button>
            </div>
            <div id="chat-container" class="chat-container"></div>
            <div id="status" class="status">Ready to start</div>
            <div id="error" class="error"></div>
        </div>
        <script>
            JAVASCRIPT_CODE_PLACEHOLDER
        </script>
    </body>
    </html>
    '''

    return html_template.replace('JAVASCRIPT_CODE_PLACEHOLDER', js_code)

def get_image_base64(image_path):
    """Convert image to base64 string"""
    with open(image_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode()
    return encoded_string

def display_header():
    """Display the header with logo"""
    try:
        header_image = get_image_base64("images/header_logo.png")
        st.markdown(f"""
            <div class="header-container">
                <img src="data:image/png;base64,{header_image}" class="header-logo" alt="Header Logo">
            </div>
        """, unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Error loading header logo: {str(e)}")

def display_footer():
    """Display the footer with logo"""
    try:
        footer_image = get_image_base64("images/footer_logo.png")
        st.markdown(f"""
            <div class="footer-container">
                <img src="data:image/png;base64,{footer_image}" class="footer-logo" alt="Footer Logo">
            </div>
        """, unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Error loading footer logo: {str(e)}")

def main():

    if not check_password():
        st.stop()

    set_page_layout()
    display_header()

    # Load article contexts
    article_contexts = load_article_contexts('resources/articles')

    # Add article selector dropdown
    article_options = ["Select Case Study..."] + list(article_contexts.keys())
    selected_article = st.selectbox(
        'Select Case Study',
        options=article_options,
        index=0,
        key='article_selector'
    )

    if selected_article != "Select Case Study...":
        selected_article_data = article_contexts.get(selected_article)
        if selected_article_data:
            # Display article content
            display_article_and_keywords(selected_article_data)

            # Create WebRTC container
            with st.container():
                st.components.v1.html(
                    get_webrtc_html(selected_article_data),
                    height=600
                )

    display_footer()

if __name__ == '__main__':
    main()

