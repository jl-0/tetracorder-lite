	subroutine setals (k)
	implicit integer*4 (i-n)

#ccc  name: setals.r
#ccc  version date: 7-24-85
#ccc  author(s): Kathy Kierein, modified R. Clark 2/2025
#ccc  language: ratfor
#ccc
#ccc  short description: This subroutine separates the line into
#ccc			 keywords and translations when storing the
#ccc			 alias words and translations into their arrays
#ccc  algorithm description: none
#ccc  system requirements: none
#ccc  subroutines called: getkey.r
#ccc  argument list description:  k = number of aliases
#ccc  parameter description:
#ccc  common description:
#ccc  message files referenced:
#ccc  internal variables: send-positions of the last non-blank in
#ccc				the line
#ccc  file description:
#ccc  user command lines:
#ccc  update information:
#ccc  NOTES:
#ccc
# This subroutine separates the lines from the stored list of alias
# words into their keywords and translations

	include "../common/spmaxes"   # max parameters, must be first

	include "../common/key1"
	include "../common/lbl4"
#RED
	integer*4 lnb     # function lnb

	integer*4 send, sbeg, comlnb
	character*1 ihbcksl

	ibeg = 1
	ihbcksl = char(92)  # this is the backslash character

	k = numals

# This finds the beginning of the keyword

	send = lnb (set)    # last non blank on the line
	comlnb = send       # if there is a comment, then this is the last character

	if (send > 1) {
		do i=1,send-1 {    # check for comment

			if (set(i:i+1) == ihbcksl // '#') {  # strip comment
				send = i -1

				# find last non blank before comment

				send = lnb(set(1:i-1))

				if(send == 0) send=1

				# put the comment into cmdcomm

				acomsiz(k) = comlnb - i + 1
				if (acomsiz(k) > 2) {
					cmdcomm(k) = set(i:comlnb)
				} else {
					acomsiz(k) = 0
				}

				break
			}
		}
	}
	do i=1,send {
		if (set (i:i) == '[') {
			sbeg = i
		}else{
			next
		}
		break
	}

	if (i == (send + 1)) {
		return
	}

# This assigns the keyword into the array cmdals and finds the size
# of the alias word

	do j=i,i+16 {
		if (set (j:j) == ']') {
			alsize(k) = j-i-1
			cmdals(k)(1:alsize(k))=set(i+1:j-1)
		}else{
			next
		}
		break
	}

# This assigns the translation into the array cmdtrn and  finds the
# size of the word

	trnsiz(k) = send - j
	if (trnsiz(k) > 75) trnsiz(k) = 75

	if (trnsiz(k) > 0) {
		cmdtrn(k) (1:trnsiz(k)) = set (j+1:send)
	} else {
		cmdtrn(k) = ' '
	}

        # now check for and strip comments

	do i=1,75 {
		cmdcomm(k)(i:i) = ' '   # initialize array
	}

	do i=1,75 {

		if (cmdtrn(k)(i:i+1) == ihbcksl // '#') {
			i2 = 75 - i -1
			cmdcomm(k)(1:i2) = cmdtrn(k)(i:75)  # put comment in cmdcomm
			
			cmdtrn(k)(i:75) = ' '   # clear comment
			# find new last non blank
			i2 = lnb (cmdtrn(k))
			if (i2 < 1) i2 = 1
			trnsiz(k) = i2
		}
	}

	return
	end
