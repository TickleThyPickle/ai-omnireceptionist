# Workato custom Ruby step: parses Claude's tool_use blocks into flat
# fields that the callable recipes consume. Extracted from
# receptionist_gmail_trigger.recipe.json (step 4).

require 'time'

body = JSON.parse(input['body'])
content = body['content']

tools = {}
content.each do |block|
  tools[block['name']] = block['input'] if block['type'] == 'tool_use'
end

reply = tools['send_reply']       || {}
log   = tools['log_conversation'] || {}
book  = tools['book_appointment'] || {}

book_datetime_raw = book['datetime'] || ''
book_datetime_parsed = book_datetime_raw.empty? ? '' : Time.parse(book_datetime_raw).iso8601
book_datetime_end = book_datetime_raw.empty? ? '' : (Time.parse(book_datetime_raw) + 3600).iso8601

{
  'reply_recipient'   => reply['recipient']    || '',
  'reply_subject'     => reply['subject']      || '',
  'reply_message'     => (reply['message']     || '').gsub("\\n", '<br>').gsub("\n", '<br>'),
  'log_sender'        => log['sender']         || '',
  'log_intent'        => log['intent']         || '',
  'log_action'        => log['action_taken']   || '',
  'book_name'         => book['name']          || '',
  'book_service'      => book['service']       || '',
  'book_datetime'     => book_datetime_parsed,
  'book_datetime_end' => book_datetime_end,
  'book_notes'        => book['notes']         || ''
}
