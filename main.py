import json
import base64
import asyncio
import logging
import datetime

import av
import streamlit as st
from streamlit_webrtc import WebRtcMode, webrtc_streamer
import websockets

from utils import (
    audio_frame_to_pcm_audio,
    pcm_audio_to_audio_frame,
    get_blank_audio_frame,
    hash_by_code
)
from st_utils import get_logger, get_event_loop
from prompt_utils import load_article_contexts, get_instructions_template, display_article_and_keywords

st.set_page_config(layout="wide", initial_sidebar_state='collapsed')

logger = get_logger(__name__)


# Configuration for calling Realtime API
REALTIME_API_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"
REALTIME_API_HEADERS = {
    'OpenAI-Beta': 'realtime=v1',
}
REALTIME_API_CONFIG = dict(
    modalities = ['text', 'audio'],
    voice = 'alloy',
    input_audio_format = 'pcm16',
    output_audio_format = 'pcm16',
    input_audio_transcription = dict(
        model = 'whisper-1',
    ),
    turn_detection = dict(
        type = 'server_vad',
        threshold = 0.5,
        prefix_padding_ms = 300,
        silence_duration_ms = 800,
    ),
    tools = [],
    tool_choice = 'auto',
    temperature = 0.6,
    max_response_output_tokens = 'inf',
)

# Audio data parameters for Realtime API
API_SAMPLE_RATE = 24000
API_SAMPLE_WIDTH = 2
API_CHANNELS = 1

# Audio data parameters for client side
CLIENT_SAMPLE_RATE = 48000
CLIENT_SAMPLE_WIDTH = 2
CLIENT_CHANNELS = 2

# Mapping for PyAV format conversion
FORMAT_MAPPING = { 2: 's16' }
LAYOUT_MAPPING = { 1: 'mono', 2: 'stereo' }


class TerminateTaskGroup(Exception):
    """Exception raised to terminate a task group."""
    def __init__(self, reason: str):
        super().__init__()
        self.reason = reason

    def __repr__(self):
        return f"{self.__class__.__name__}(reason={repr(self.reason)})"


class OpenAIRealtimeAPIWrapper:
    _api_key: str
    _session_timeout: int | float
    _send_interval: float
    _recording: bool
    _messages: list[dict]
    _resampler_for_api: av.audio.resampler.AudioResampler
    _resampler_for_client: av.audio.resampler.AudioResampler
    _record_stream: av.audio.fifo.AudioFifo
    _play_stream: av.audio.fifo.AudioFifo



    def __init__(
        self,
        api_key: str,
        initial_context: str = None,
        session_timeout: int | float = 60,
        send_interval: float = 0.2
    ):
        """
        Args:
            api_key (str): OpenAI API key
            initial_context (str): Initial context to start the conversation
            session_timeout (int | float): Voice chat session timeout duration (seconds)
            send_interval (float): Interval for sending voice data (seconds)
        """
        self._api_key = api_key
        self._initial_context = initial_context
        self._session_timeout = session_timeout
        self._send_interval = send_interval
        self._config = REALTIME_API_CONFIG.copy()  # Create a local copy

        self._recording = False
        self._messages = []
        self._conversation_started = False
        self._resampler_for_api = av.audio.resampler.AudioResampler(
            format=FORMAT_MAPPING[API_SAMPLE_WIDTH],
            layout=LAYOUT_MAPPING[API_CHANNELS],
            rate=API_SAMPLE_RATE
        )
        self._resampler_for_client = av.audio.resampler.AudioResampler(
            format=FORMAT_MAPPING[CLIENT_SAMPLE_WIDTH],
            layout=LAYOUT_MAPPING[CLIENT_CHANNELS],
            rate=CLIENT_SAMPLE_RATE
        )
    def update_instructions(self, instructions: str):
        """Update the instructions in the config"""
        self._config['instructions'] = instructions
        self._initial_context = instructions  # Update initial context as well

    def audio_frame_callback(self, frame: av.AudioFrame) -> av.AudioFrame:
        """Audio data processing callback function for streamlit-webrtc

        Args:
            frame (av.AudioFrame): Audio data frame
        Returns:
            av.AudioFrame: Processed audio data frame
        """
        stream_pts = self._record_stream.samples_written * self._record_stream.pts_per_sample
        if frame.pts > stream_pts:
            logger.debug('Missing samples: %s < %s; Filling them up...', stream_pts, frame.pts)
            blank_frame = get_blank_audio_frame(
                format = frame.format.name,
                layout = frame.layout.name,
                samples = int((frame.pts - stream_pts) / self._record_stream.pts_per_sample),
                sample_rate = frame.sample_rate
            )
            self._record_stream.write(blank_frame)
        self._record_stream.write(frame)

        new_frame = self._play_stream.read(frame.samples, partial = True)
        if new_frame:
            assert new_frame.format.name == frame.format.name
            assert new_frame.layout.name == frame.layout.name
            assert new_frame.sample_rate == frame.sample_rate
        else:
            # Return silence if empty
            new_frame = get_blank_audio_frame(
                format = frame.format.name,
                layout = frame.layout.name,
                samples = frame.samples,
                sample_rate = frame.sample_rate
            )
        new_frame.pts = frame.pts
        new_frame.time_base = frame.time_base
        return new_frame

    async def run(self):
        """Start connection with OpenAI Realtime API and handle audio data transmission
        """
        if self.recording:
            logger.warning('Already recording')
            return

        self.start()

        async with websockets.connect(
            REALTIME_API_URL,
            additional_headers = {
                'Authorization': f"Bearer {self._api_key}",
                **REALTIME_API_HEADERS
            }
        ) as websocket:
            logger.info('Connected to OpenAI Realtime API')
            await self.configure(websocket)
            logger.info('Configured')

            try:
                async with asyncio.TaskGroup() as task_group:
                    task_group.create_task(self.send(websocket))
                    task_group.create_task(self.receive(websocket))
                    task_group.create_task(self.timer())
                    task_group.create_task(self.status_checker())
            except* TerminateTaskGroup as eg:
                logger.info('Connection closing: %s', eg.exceptions[0].reason)
            except* Exception as eg:
                logger.error('Error in task group', exc_info = eg)
        logger.info('Connection closed')



    async def configure(self, websocket: 'websockets.asyncio.client.ClientConnection'):
        """Send session configuration to OpenAI Realtime API and initialize conversation"""
        # Configure session with local config
        await websocket.send(json.dumps(dict(
            type='session.update',
            session=self._config,
        )))

        # If initial context is provided, start the conversation
        if self._initial_context:
            st.write('Starting conversation...')
            # Send the initial message
            await websocket.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": self._initial_context,
                        }
                    ]
                }
            }))

            # Add context message to the message history
            self._messages.append({
                "role": "user",
                "content": self._initial_context
            })

            # Trigger the assistant's response
            await websocket.send(json.dumps({
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"]  # Include both modalities since your config uses both
                }
            }))
    # async def configure(self, websocket: 'websockets.asyncio.client.ClientConnection'):
    #     """Send session configuration to OpenAI Realtime API and initialize conversation"""
    #     # Configure session
    #     await websocket.send(json.dumps(dict(
    #         type='session.update',
    #         session=REALTIME_API_CONFIG,
    #     )))
    #
    #     # If initial context is provided, start the conversation
    #     if self._initial_context:
    #         st.write('Starting conversation...')
    #         await websocket.send(json.dumps({
    #             "type": "conversation.item.create",
    #             "item": {
    #                 "type": "message",
    #                 "role": "user",
    #                 "content": [
    #                     {
    #                         "type": "input_text",
    #                         "text": self._initial_context,
    #                     }
    #                 ]
    #             }
    #         }))
    #
    #         # Add context message to the message history
    #         self._messages.append({
    #             "role": "user",
    #             "content": self._initial_context
    #         })
    async def send(self, websocket: 'websockets.asyncio.client.ClientConnection'):
        """Send audio data to OpenAI Realtime API

        Args:
            websocket (websockets.asyncio.client.ClientConnection): WebSocket client
        """
        while True:
            try:
                frame = self._record_stream.read()
                if not frame:
                    await asyncio.sleep(self._send_interval)
                    continue
                frame, *_rest = self._resampler_for_api.resample(frame)
                assert not _rest

                pcm_audio = audio_frame_to_pcm_audio(frame)
                base64_audio = base64.b64encode(pcm_audio).decode('utf-8')

                await websocket.send(json.dumps(dict(
                    type = 'input_audio_buffer.append',
                    audio = base64_audio
                )))
                logger.debug('Sent audio to OpenAI (%d bytes)', len(pcm_audio))
            except Exception as e:
                logger.error('Error in send loop', exc_info = e)
                st.exception(e)
                break
        raise TerminateTaskGroup('send')

    async def receive(self, websocket: 'websockets.asyncio.client.ClientConnection'):
        """Receive responses from OpenAI Realtime API

        Args:
            websocket (websockets.asyncio.client.ClientConnection): WebSocket client
        """
        transcript_placeholder = None
        message = None
        user_transcript_placeholder = None
        user_message = None
        while True:
            try:
                response = await websocket.recv()
                if response:
                    response_data = json.loads(response)

                    if response_data['type'] == 'response.audio.delta':
                        # Queue audio data from server
                        base64_audio = response_data['delta']
                        if base64_audio:
                            pcm_audio = base64.b64decode(base64_audio)
                            frame = pcm_audio_to_audio_frame(
                                pcm_audio,
                                format = FORMAT_MAPPING[API_SAMPLE_WIDTH],
                                layout = LAYOUT_MAPPING[API_CHANNELS],
                                sample_rate = API_SAMPLE_RATE
                            )
                            resampled_frame, *_rest = \
                                    self._resampler_for_client.resample(frame)
                            assert not _rest
                            self._play_stream.write(resampled_frame)
                            logger.debug(
                                'Event: %s - received audio from OpenAI (%d bytes)',
                                response_data['type'],
                                len(pcm_audio)
                            )

                    elif response_data['type'] == 'response.audio_transcript.delta':
                        # logger.debug('Event: %s', response_data['type'])  # Skipped as it occurs too frequently
                        if not message:
                            transcript_placeholder = st.empty()
                            message = dict(role = 'assistant', content = '')
                            self._messages.append(message)
                        message['content'] += response_data['delta']
                        if not transcript_placeholder:
                            transcript_placeholder = st.empty()
                        with transcript_placeholder.container():
                            with st.chat_message('assistant'):
                                st.write(message['content'])

                    elif response_data['type'] == 'response.audio_transcript.done':
                        logger.info(
                            'Event: %s - %s',
                            response_data['type'],
                            response_data['transcript']
                        )
                        message = None
                        transcript_placeholder = None

                    elif response_data['type'] == 'conversation.item.input_audio_transcription.completed':
                        logger.debug(
                            'Event: %s - %s',
                            response_data['type'],
                            response_data['transcript']
                        )
                        if not user_message:
                            user_message = dict(role = 'user', content = '')
                            self._messages.append(user_message)
                        if user_message['content'] is None:
                            user_message['content'] = response_data['transcript']
                        else:
                            user_message['content'] += response_data['transcript']
                        if not user_transcript_placeholder:
                            user_transcript_placeholder = st.empty()
                        with user_transcript_placeholder.container():
                            with st.chat_message('user'):
                                st.write(user_message['content'])

                    elif response_data['type'] == 'input_audio_buffer.speech_started':
                        # Reset existing AI voice audio when user speech is detected
                        self.reset_stream(play_stream_only = True)
                        logger.debug(
                            'Event: %s - cleared the play stream',
                            response_data['type']
                        )
                        # Prepare container when user starts speaking to avoid overlap with AI transcript
                        user_transcript_placeholder = st.empty()
                        user_message = dict(role = 'user', content = None)
                        self._messages.append(user_message)

                    elif response_data['type'] == 'error':
                        logger.error('Event: %s - %s', response_data['type'], response_data)
                        st.error(response_data['error']['message'])

                    elif any(
                        response_data['type'].startswith(pattern)
                         for pattern in (
                            'session.created',
                            'session.updated',
                            'conversation.item.created',
                            'response.done',
                            'response.audio.',
                            'rate_limits.updated',
                        )
                    ):
                        # Log content
                        logger.debug('%s: %s', response_data['type'], response_data)
                    else:
                        # Only log event name
                        logger.debug('Event: %s', response_data['type'])
                else:
                    logger.debug('No response')
            except Exception as e:
                logger.error('Error in receive loop', exc_info = e)
                st.exception(e)
                break
        raise TerminateTaskGroup('receive')

    async def timer(self):
        """Monitor session timeout
        """
        await asyncio.sleep(
            datetime.timedelta(seconds = self._session_timeout).total_seconds()
        )
        raise TerminateTaskGroup('timer')

    async def status_checker(self):
        """Monitor recording status and terminate task group when recording ends
        """
        while self.recording:
            await asyncio.sleep(1)
        logger.info('Recording stopped')
        raise TerminateTaskGroup('status_checker')

    def write_messages(self):
        """Display chat messages, excluding the initial instructions"""
        for message in self.valid_messages:
            # Skip the first user message which contains the instructions
            if message['role'] == 'user' and message['content'] == self._initial_context:
                continue
            with st.chat_message(message['role']):
                st.write(message['content'])

    @property
    def recording(self) -> bool:
        """Get recording status of audio data
        """
        return self._recording

    @property
    def valid_messages(self) -> list[dict]:
        """Get valid chat messages
        """
        return [m for m in self._messages if m['content'] is not None]

    def set_session_timeout(self, timeout: int | float):
        """Set session timeout duration
        """
        self._session_timeout = timeout

    def start(self):
        """Start operation

        (Automatically called by run method)
        """
        if self.recording:
            raise RuntimeError('Already recording')
        self._recording = True
        self._messages = []
        self.reset_stream()

    def stop(self):
        """Stop operation
        """
        self._recording = False

    def reset_stream(self, play_stream_only: bool = False):
        """Reset audio data stream
        """
        if not play_stream_only:
            self._record_stream = av.audio.fifo.AudioFifo()
        self._play_stream = av.audio.fifo.AudioFifo()


def check_password():
    """Returns `True` if the user had the correct password."""

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if st.session_state["password"] == st.secrets["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Don't store password
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # First run, show input for password
        st.text_input(
            "Password", type="password", on_change=password_entered, key="password"
        )
        return False
    elif not st.session_state["password_correct"]:
        # Password incorrect, show input + error
        st.text_input(
            "Password", type="password", on_change=password_entered, key="password"
        )
        st.error("😕 Password incorrect")
        return False
    else:
        # Password correct
        return True

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
                padding: 0.5rem 0;  /* Reduced padding */
                box-shadow: 0 -1px 3px rgba(0,0,0,0.05);  /* Subtler shadow */
            }

            .footer-logo {
                max-width: 10px;  /* Smaller logo */
                height: auto;
                opacity: 0.7;  /* Slightly transparent */
            }

            /* Add hover effect */
            .footer-logo:hover {
                opacity: 1;
                transition: opacity 0.3s ease;
            }

            /* Add padding to main content to prevent overlap with footer */
            .main-content {
                padding-bottom: 100px;  /* Reduced padding to match smaller footer */
            }
        </style>
    """, unsafe_allow_html=True)

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
    # Set up page layout and styles
    set_page_layout()

    # Display header
    display_header()

    if not check_password():
            st.stop()
    loop = get_event_loop(_logger = logger)

    # Load article contexts from the articles directory
    article_contexts = load_article_contexts('resources/articles')

    # Initialize API wrapper first
    api_wrapper_key = f"api_wrapper-{hash_by_code(OpenAIRealtimeAPIWrapper)}"
    if api_wrapper_key not in st.session_state:
        openai_api_key = st.secrets['OPENAI_API_KEY']
        st.session_state[api_wrapper_key] = OpenAIRealtimeAPIWrapper(
            api_key=openai_api_key,
            initial_context=None  # Start with no context
        )
    api_wrapper = st.session_state[api_wrapper_key]

    # Add article selector dropdown with an empty default option
    article_options = ["Select Case Study..."] + list(article_contexts.keys())
    selected_article = st.selectbox(
        'Select Case Stufy',
        options=article_options,
        index=0,  # Default to the first option ("Select an article...")
        key='article_selector'
    )

    # Only show content if a valid article is selected
    if selected_article != "Select Case Study...":
        # Get the selected article data and update instructions
        selected_article_data = article_contexts.get(selected_article)
        if selected_article_data:
            # Display the article
            display_article_and_keywords(selected_article_data)

            # Update the instructions in the API wrapper
            new_instructions = get_instructions_template(selected_article_data['content'])
            api_wrapper.update_instructions(new_instructions)

            # Show conversation controls
            session_timeout = st.slider(
                'Maximum conversation time (seconds)',
                min_value=60,
                max_value=300,
                value=120
            )
            api_wrapper.set_session_timeout(session_timeout)

            # Conversation start/end buttons
            if 'recording' not in st.session_state:
                st.session_state.recording = False
            if st.session_state.recording:
                if st.button('End conversation', type='primary'):
                    st.session_state.recording = False
            else:
                if st.button('Start conversation (it may take time...)'):
                    st.session_state.recording = True


            # with st.expander("Audio input settings", expanded=False):
            # with st.sidebar:

            # WebRTC streamer setup
            webrtc_ctx = webrtc_streamer(
                    key = f"recoder",
                    mode = WebRtcMode.SENDRECV,
                    rtc_configuration = dict(
                        iceServers = [
                            dict(urls = ['stun:stun.l.google.com:19302'])
                        ]
                    ),
                    audio_frame_callback = api_wrapper.audio_frame_callback,
                    media_stream_constraints = dict(video = False, audio = True),
                    desired_playing_state = st.session_state.recording,
                )

            if webrtc_ctx.state.playing:
                if not api_wrapper.recording:
                    st.write('Connecting to OpenAI.')
                    logger.info('Starting running')
                    loop.run_until_complete(api_wrapper.run())
                    logger.info('Finished running')
                    st.write('Disconnected from OpenAI.')
                    st.session_state.recording = False
                    st.rerun()
            else:
                if api_wrapper.recording:
                    logger.info('Stopping running')
                    api_wrapper.stop()
                    st.session_state.recording = False
                    st.rerun()
                api_wrapper.write_messages()

    display_footer()


if __name__ == '__main__':
    logging.basicConfig(
        format = "%(levelname)s %(name)s@%(filename)s:%(lineno)d: %(message)s",
    )

    st_webrtc_logger = logging.getLogger('streamlit_webrtc')
    st_webrtc_logger.setLevel(logging.DEBUG)

    aioice_logger = logging.getLogger('aioice')
    aioice_logger.setLevel(logging.WARNING)

    fsevents_logger = logging.getLogger('fsevents')
    fsevents_logger.setLevel(logging.WARNING)

    main()
